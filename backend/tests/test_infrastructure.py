from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.main import app, Incident
from backend.app.security import current_user
from backend.app import workspace
from backend.app.services import infrastructure_analysis as ai, infrastructure_detector as detector
from backend.app.services.gemini_infrastructure import Interpretation, SYSTEM
from backend.app.services.infrastructure_location import resolve_municipality


def test_model_is_cached_and_all_boxes_are_returned(monkeypatch):
    class Tensor:
        def __init__(self, value): self.value=value
        def tolist(self): return self.value
    model=SimpleNamespace(names=dict(enumerate(detector.CLASSES)))
    model.predict=lambda **kwargs: [SimpleNamespace(names={0:'pothole',1:'pipe-burst'},boxes=SimpleNamespace(
        xyxy=Tensor([[1,2,20,25],[20,20,40,40]]),conf=Tensor([.92,.5]),cls=Tensor([0,1])))]
    monkeypatch.setattr(detector,'_model',model)
    result=detector.detect(Image.new('RGB',(50,50)))
    assert len(result['detections'])==2
    assert result['primary_detection']['category']=='roads'
    assert result['requires_manual_review'] is False
    assert result['annotated_image'].startswith('data:image/jpeg;base64,')
    assert detector._model is model


def test_low_confidence_and_resident_rejection_trigger_gemini(monkeypatch):
    calls=[]
    monkeypatch.setattr(detector,'detect',lambda image:{'detections':[], 'requires_manual_review':True})
    monkeypatch.setattr(ai.gemini_infrastructure,'interpret',lambda *args: calls.append(args) or {'available':True,'category':'electricity'})
    assert ai.analyze(Image.new('RGB',(10,10)))['category']=='electricity'
    ai.analyze(None,'Broken streetlight',True)
    assert calls[-1][2] is True


def test_ai_outage_preserves_manual_submission(monkeypatch):
    def unavailable(*args): raise RuntimeError('unavailable')
    monkeypatch.setattr(detector,'detect',unavailable)
    monkeypatch.setattr(ai.gemini_infrastructure,'interpret',unavailable)
    assert ai.analyze(Image.new('RGB',(10,10)))['available'] is False
    report=Incident(category='water',title='Burst pipe',description='Water is flowing from the pipe.',location='Main road')
    assert report.category=='water'


def test_gemini_schema_rejects_unapproved_category_or_final_decision():
    data=dict(issue_title='Streetlight fault',category='electricity',description='Resident reports a broken streetlight.',
              observed_information=[],resident_reported_information=['Broken light'],assumptions=[],
              recommended_department='Electricity',suggested_priority='normal',requires_human_review=True)
    assert Interpretation(**data).requires_human_review is True
    for update in ({'category':'unrelated'},{'requires_human_review':False},{'secret':'bad'}):
        with pytest.raises(ValueError): Interpretation(**{**data,**update})
    assert 'untrusted evidence' in SYSTEM


@pytest.mark.parametrize('patch',[{'latitude':-26},{'category':'invalid'},{'latitude':100,'longitude':28}])
def test_report_validation(patch):
    with pytest.raises(ValueError):
        Incident(**{'category':'water','title':'Burst pipe','description':'A burst pipe on the road.','location':'Main road',**patch})


def test_municipal_queries_filter_by_department_before_aggregating(monkeypatch):
    calls=[]
    monkeypatch.setattr(workspace,'_execute',lambda sql,params,**kwargs:calls.append((sql,params)) or [])
    user={'id':'00000000-0000-0000-0000-000000000001'}
    workspace._municipal_execute(user,'select count(*) from public.incidents where category=%s',('water',))
    assert 'public.authorized_incidents(' in calls[0][0]
    assert calls[0][1]==('water',)


@pytest.mark.parametrize(('category','expected_count'), [('water',2),('electricity',0)])
def test_municipal_map_applies_category_and_returns_all_location_fields(monkeypatch,category,expected_count):
    calls=[]
    points=[
        {'id':'water-1','category':'water','location_text':'Main Road','latitude':-25.75,'longitude':28.2},
        {'id':'water-2','category':'water','location_text':'Park Street','latitude':-25.76,'longitude':28.21},
    ] if category=='water' else []
    def execute(user,sql,params=(),*,fetch='all'):
        calls.append((sql,params,fetch))
        return {'total':expected_count} if fetch=='one' else points
    monkeypatch.setattr(workspace,'_municipal_execute',execute)
    result=workspace.municipal_map({'id':'municipal-user','municipality_ids':['municipality-id']},category=category)
    assert result['total']==expected_count
    assert len(result['items'])==expected_count
    assert all(item['latitude'] is not None and item['longitude'] is not None for item in result['items'])
    assert all('category=%s' in sql and category in params for sql,params,_ in calls)
    assert 'location_text' in calls[1][0] and ' as latitude' in calls[1][0] and ' as longitude' in calls[1][0]


class Connection:
    def __init__(self,rows): self.rows=iter(rows)
    def execute(self,*args):
        value=next(self.rows)
        return SimpleNamespace(fetchall=lambda:value,fetchone=lambda:value)


def test_boundary_wins_and_mismatch_is_rejected():
    with pytest.raises(HTTPException) as exc:
        resolve_municipality(Connection([[{'id':'a','name':'A','municipality_type':'local'}]]),-26,28,'b')
    assert exc.value.status_code==422
    assert resolve_municipality(Connection([[{'id':'a'}]]),-26,28,'a')=='a'


def test_no_boundaries_requires_valid_resident_confirmation():
    with pytest.raises(HTTPException): resolve_municipality(Connection([[]]),-26,28,None)
    assert resolve_municipality(Connection([[],{'id':'a'}]),-26,28,'a')=='a'


def test_infrastructure_routes_require_authentication():
    with TestClient(app) as client:
        assert client.post('/api/v1/infrastructure/analyse',data={'description':'Broken streetlights'}).status_code==401
        assert client.get('/api/v1/infrastructure/departments/my').status_code==401
        assert client.get('/api/v1/infrastructure/configuration').status_code==401
