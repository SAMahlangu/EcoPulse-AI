"""Single cached custom YOLO model; serialized inference, no automatic downloads."""
from __future__ import annotations

import base64
import os
import threading
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

CLASSES = {
    'damaged-sign': ('roads', 'Damaged road sign'),
    'electricity-pole-damage': ('electricity', 'Electricity pole damage'),
    'fallen-trees': ('environmental', 'Fallen tree'),
    'graffiti': ('other', 'Graffiti'),
    'pipe-burst': ('water', 'Burst pipe'),
    'pothole': ('roads', 'Pothole'),
    'thrash': ('waste', 'Rubbish dumping'),
}
_model = None
_lock = threading.Lock()


def thresholds():
    low = float(os.getenv('YOLO_MIN_CONFIDENCE', '0.25'))
    confirm = float(os.getenv('YOLO_CONFIRM_CONFIDENCE', '0.40'))
    high = float(os.getenv('YOLO_HIGH_CONFIDENCE', '0.70'))
    if not 0 <= low <= confirm <= high <= 1:
        raise ValueError('Invalid YOLO confidence thresholds')
    return low, confirm, high


def detect(image: Image.Image) -> dict:
    global _model
    low, confirm, high = thresholds()
    with _lock:
        if _model is None:
            config = Path(__file__).resolve().parents[2] / '.ultralytics'
            config.mkdir(exist_ok=True)
            os.environ.setdefault('YOLO_CONFIG_DIR', str(config))
            from ultralytics import YOLO
            path = Path(os.getenv('YOLO_MODEL_PATH', str(Path(__file__).resolve().parents[2] / 'models' / 'best.pt')))
            if not path.is_absolute():
                path = Path(__file__).resolve().parents[2] / path
            if not path.is_file():
                raise FileNotFoundError('Configured infrastructure model is missing')
            model = YOLO(str(path))
            if set(model.names.values()) != set(CLASSES):
                raise ValueError('Model class names do not match the seven infrastructure classes')
            _model = model
        result = _model.predict(source=image, conf=low, max_det=50, device=os.getenv('YOLO_DEVICE', 'cpu'), verbose=False)[0]
        detections = []
        for box, confidence, class_id in zip(result.boxes.xyxy.tolist(), result.boxes.conf.tolist(), result.boxes.cls.tolist()):
            name = result.names[int(class_id)]
            category, label = CLASSES[name]
            detections.append({'class_name': name, 'label': label, 'category': category,
                               'confidence': round(confidence, 4), 'bounding_box': [round(x, 1) for x in box],
                               'confidence_level': 'high' if confidence >= high else 'confirmation' if confidence >= confirm else 'uncertain'})
    detections.sort(key=lambda item: item['confidence'], reverse=True)
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    for item in detections:
        draw.rectangle(item['bounding_box'], outline='#f59e0b', width=4)
        draw.text(tuple(item['bounding_box'][:2]), f"{item['label']} {item['confidence']:.0%}", fill='#f59e0b')
    buffer = BytesIO()
    annotated.save(buffer, format='JPEG', quality=85)
    primary = detections[0] if detections else None
    return {'success': True, 'detections': detections, 'primary_detection': primary,
            'requires_manual_review': primary is None or primary['confidence'] < high,
            'annotated_image': 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode(),
            'image_width': image.width, 'image_height': image.height,
            'model_name': 'EcoPulse infrastructure YOLO', 'model_version': 'best.pt'}
