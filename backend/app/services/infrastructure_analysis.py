"""YOLO first, Gemini for uncertainty, descriptions, or resident corrections."""
from . import infrastructure_detector, gemini_infrastructure


def analyze(image=None, description='', rejected=False, detect_only=False):
    detection = {}
    if image is not None and not rejected:
        try:
            detection = infrastructure_detector.detect(image)
        except Exception:
            detection = {'success': False, 'detections': [], 'requires_manual_review': True}
    primary = detection.get('primary_detection')
    yolo = None
    if primary:
        yolo = {**detection, 'available': True, 'category': primary['category'],
                'subcategory': primary['label'], 'confidence': primary['confidence'],
                'observations': [f"{d['label']} detected ({d['confidence']:.0%} confidence)" for d in detection['detections']],
                'explanation': 'Review the highlighted infrastructure issue. Model confidence does not verify the finding.',
                'detection_method': 'yolo'}
    if not detect_only and (rejected or description.strip() or not primary or detection.get('requires_manual_review')):
        try:
            result = gemini_infrastructure.interpret(image, description, rejected)
            return {**detection, **result, 'detection_method': 'hybrid' if primary else 'gemini'}
        except Exception:
            if yolo:
                yolo['fallback_unavailable'] = True
    if yolo:
        return yolo
    return {**detection, 'available': False, 'model_name': 'Infrastructure AI', 'model_version': 'best.pt',
            'reason': "We couldn't confidently identify this issue. Please describe it or choose a category manually.",
            'requires_manual_review': True}
