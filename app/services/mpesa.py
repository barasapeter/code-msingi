"""Asynchronous Safaricom Daraja STK Push client."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

import httpx

from app.config import get_settings

TOKEN_URL = (
    "https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
)
STK_PUSH_URL = "https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest"


class MpesaApiError(RuntimeError):
    """Safe provider error that may be displayed to the customer."""


def _provider_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = {}
    if isinstance(body, dict):
        for field in (
            "errorMessage",
            "ResponseDescription",
            "CustomerMessage",
            "message",
            "detail",
        ):
            message = body.get(field)
            if isinstance(message, str) and message.strip():
                return message.strip()
    return f"M-Pesa rejected the request (HTTP {response.status_code})."


def normalize_kenyan_phone(phone: str) -> str:
    digits = "".join(character for character in phone if character.isdigit())
    if digits.startswith("0"):
        digits = f"254{digits[1:]}"
    if len(digits) != 12 or not digits.startswith("254"):
        raise ValueError("Enter a valid phone number.")
    return digits


async def initiate_stk_push(
    *, phone: str, amount: float, account_reference: str, description: str
) -> dict[str, Any]:
    """Request an STK push from Daraja without blocking the FastAPI event loop."""
    settings = get_settings()
    required = (
        settings.mpesa_consumer_key,
        settings.mpesa_consumer_secret,
        settings.mpesa_shortcode,
        settings.mpesa_passkey,
        settings.mpesa_callback_url,
    )
    if not all(required):
        raise RuntimeError(
            "M-Pesa is not configured. Set all MPESA_* environment variables."
        )

    phone_number = normalize_kenyan_phone(phone)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password_source = f"{settings.mpesa_shortcode}{settings.mpesa_passkey}{timestamp}"
    password = base64.b64encode(password_source.encode()).decode()

    async with httpx.AsyncClient(timeout=30.0) as client:
        token_response = await client.get(
            TOKEN_URL,
            auth=(settings.mpesa_consumer_key, settings.mpesa_consumer_secret),
        )
        if token_response.is_error:
            raise MpesaApiError(_provider_message(token_response))
        try:
            access_token = token_response.json()["access_token"]
        except (KeyError, ValueError) as exc:
            raise MpesaApiError("M-Pesa did not return a valid access token.") from exc
        request_body = {
            "BusinessShortCode": settings.mpesa_shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerBuyGoodsOnline",
            "Amount": amount,
            "PartyA": phone_number,
            "PartyB": 4858770,
            "PhoneNumber": phone_number,
            "CallBackURL": settings.mpesa_callback_url,
            "AccountReference": account_reference[:12],
            "TransactionDesc": description[:13],
        }
        response = await client.post(
            STK_PUSH_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json=request_body,
        )

        if response.is_error:
            raise MpesaApiError(_provider_message(response))
        try:
            result = response.json()
        except ValueError as exc:
            raise MpesaApiError(
                "M-Pesa returned an unreadable payment response."
            ) from exc
        if result.get("ResponseCode") not in (None, "0", 0):
            raise MpesaApiError(_provider_message(response))
        return result
