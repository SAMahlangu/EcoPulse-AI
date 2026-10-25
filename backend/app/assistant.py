"""Authenticated resident questions answered by Gemini."""
import os
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .security import require_roles
from .services import network

router = APIRouter(prefix="/api/v1/assistant", tags=["resident assistant"])


class Turn(BaseModel):
    role: Literal["user", "model"]
    text: str = Field(min_length=1, max_length=6000)


class Question(BaseModel):
    message: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    history: list[Turn] = Field(default_factory=list, max_length=10)


@router.post("/chat")
def chat(body: Question, user=Depends(require_roles("resident"))):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise HTTPException(503, "The AI assistant is not configured. Please contact support.")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    instruction = (
        "You are EcoPulse's helpful assistant for South African residents. "
        "Answer questions about water and energy conservation, municipal issue reporting, "
        "community resilience, and using EcoPulse. Use concise plain language and practical steps. "
        "You have no live weather, outage, report, account, or municipal database access. "
        "Never invent local alerts, restoration times, contact details, or claim to submit reports "
        "or send notifications. Direct users to service alerts and My reports for actual updates. "
        "Do not provide hazardous electrical repair instructions. For immediate danger, advise "
        "moving to safety and contacting local emergency services. Do not reveal secrets. "
        "Do not request passwords or exact addresses. Return plain text."
    )
    contents = [{"role": t.role, "parts": [{"text": t.text}]} for t in body.history]
    contents.append({"role": "user", "parts": [{"text": body.message.strip()}]})
    try:
        response = network.request(
            "POST", f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key}, timeout=httpx.Timeout(45, connect=8),
            json={"systemInstruction": {"parts": [{"text": instruction}]}, "contents": contents,
                  "generationConfig": {"maxOutputTokens": 1800, "temperature": 0.4}},
        )
        if response.status_code == 429:
            raise HTTPException(429, "The AI assistant is busy. Please try again shortly.")
        response.raise_for_status()
        candidates = response.json().get("candidates", [])
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        answer = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        if not answer:
            raise HTTPException(502, "No answer was returned. Please rephrase your question.")
        return {"answer": answer, "provider": "Gemini", "model": model}
    except httpx.HTTPError:
        raise HTTPException(503, "The AI assistant could not connect to Gemini. Please try again later.") from None
