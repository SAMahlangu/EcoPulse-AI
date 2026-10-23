"""Fixed-destination Supabase Auth gateway; password/JWT checks stay upstream."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import OrderedDict, deque
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from starlette.concurrency import run_in_threadpool

from .services import network

router=APIRouter(prefix='/api/v1/auth/gateway')
_attempts: OrderedDict[str,deque]=OrderedDict()
_lock=threading.Lock()


class AuthBody(BaseModel):
    model_config=ConfigDict(extra='forbid')


class PasswordLogin(AuthBody):
    email: str=Field(min_length=3,max_length=254)
    password: SecretStr=Field(min_length=1,max_length=1024)


class RefreshLogin(AuthBody):
    refresh_token: SecretStr=Field(min_length=1,max_length=8192)


class PublicProfile(AuthBody):
    full_name: str=Field(min_length=2,max_length=120)


class Signup(PasswordLogin):
    data: PublicProfile


class Recover(AuthBody):
    email: str=Field(min_length=3,max_length=254)
    redirectTo: str|None=Field(default=None,max_length=2048)


class ChangePassword(AuthBody):
    password: SecretStr=Field(min_length=8,max_length=128)


def limit_attempts(request:Request):
    actor=request.client.host if request.client else 'unknown'
    now=time.monotonic()
    with _lock:
        attempts=_attempts.setdefault(actor,deque())
        _attempts.move_to_end(actor)
        while attempts and attempts[0]<now-60:attempts.popleft()
        if len(attempts)>=30:raise HTTPException(429,'Please wait a minute before trying to sign in again.',headers={'Retry-After':'60','Cache-Control':'no-store'})
        attempts.append(now)
        while len(_attempts)>1000:_attempts.popitem(last=False)


@router.api_route('/{action}',methods=['POST','PUT'])
async def auth_gateway(action:Literal['token','signup','recover','logout','user'],request:Request,
                       grant_type:Literal['password','refresh_token']|None=Query(None)):
    limit_attempts(request)
    if request.method!=('PUT' if action=='user' else 'POST'):
        raise HTTPException(405,'Unsupported authentication method.')
    base=os.getenv('SUPABASE_URL','').strip().rstrip('/')
    key=os.getenv('SUPABASE_ANON_KEY','').strip()
    if not base or not key or urlsplit(base).scheme!='https':
        raise HTTPException(503,'The sign-in service is not configured.')
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>16384:raise HTTPException(413,'Authentication request is too large.')
    try:
        raw=json.loads(data) if data else {}
        params={}
        headers={'apikey':key,'Content-Type':'application/json'}
        if action=='token':
            if grant_type=='password':
                model=PasswordLogin.model_validate(raw)
                body={'email':model.email,'password':model.password.get_secret_value()}
            elif grant_type=='refresh_token':
                model=RefreshLogin.model_validate(raw)
                body={'refresh_token':model.refresh_token.get_secret_value()}
            else:raise HTTPException(422,'Choose a supported sign-in method.')
            params['grant_type']=grant_type
        elif action=='signup':
            model=Signup.model_validate(raw)
            body={'email':model.email,'password':model.password.get_secret_value(),'data':model.data.model_dump()}
        elif action=='recover':
            model=Recover.model_validate(raw)
            body={'email':model.email}
            if model.redirectTo:
                parsed=urlsplit(model.redirectTo)
                origin=f'{parsed.scheme}://{parsed.netloc}'
                origins={value.strip() for value in os.getenv('CORS_ORIGINS','http://localhost:5173').split(',')}
                loopback=re.fullmatch(r'http://(?:localhost|127\.0\.0\.1|\[::1\]):(?:517[3-9]|4173)',origin)
                if origin not in origins and not (loopback and 'http://localhost:5173' in origins):
                    raise HTTPException(422,'Use the configured website address for password recovery.')
                if parsed.username or parsed.password:raise HTTPException(422,'Invalid recovery address.')
                params['redirect_to']=model.redirectTo
        else:
            authorization=request.headers.get('authorization','')
            if not authorization.startswith('Bearer ') or len(authorization)>16384:
                raise HTTPException(401,'A valid session is required.')
            headers['Authorization']=authorization
            if action=='user':
                body={'password':ChangePassword.model_validate(raw).password.get_secret_value()}
            else:
                AuthBody.model_validate(raw)
                body={}
    except (ValidationError,ValueError,TypeError):
        # Never return Pydantic's input fields: they can contain passwords/tokens.
        raise HTTPException(422,'Check the authentication details and try again.') from None
    try:
        result=await run_in_threadpool(network.request,request.method,f'{base}/auth/v1/{action}',
                                      headers=headers,json=body,params=params,timeout=15)
        if result.status_code==204:return Response(status_code=204,headers={'Cache-Control':'no-store'})
        return JSONResponse(result.json(),status_code=result.status_code,
                            headers={'Cache-Control':'no-store','Pragma':'no-cache'})
    except (httpx.HTTPError,ValueError):
        raise HTTPException(503,'The sign-in service is temporarily unreachable. Please retry.',headers={'Cache-Control':'no-store'}) from None
