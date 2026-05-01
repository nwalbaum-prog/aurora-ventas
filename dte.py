# dte.py — Integración Bsale para emisión de boleta electrónica
import urllib.request
import urllib.error
import json

_BSALE_API_URL = "https://api.bsale.io/v1/documents.json"


def emit_boleta(items: list, total: float, config: dict) -> dict:
    """
    Emite una boleta electrónica via Bsale API.

    items:  [{"nombre": str, "cantidad": float, "precio_unitario": float}]
    total:  float — total a cobrar (con IVA incluido)
    config: {"bsale_token": str, "bsale_document_type_id": int, "bsale_price_list_id": int}

    Retorna: {"ok": bool, "folio": int|None, "pdf_url": str|None,
              "numero": str|None, "error": str|None}
    """
    token = config.get('bsale_token', '').strip()
    if not token:
        return {"ok": False, "folio": None, "pdf_url": None, "numero": None,
                "error": "DTE no configurado"}

    doc_type_id   = int(config.get('bsale_document_type_id', 39))
    price_list_id = int(config.get('bsale_price_list_id', 1))

    body = {
        "documentTypeId": doc_type_id,
        "officeId":       1,
        "priceListId":    price_list_id,
        "details": [
            {
                "quantity":      item["cantidad"],
                "comment":       item["nombre"],
                "grossUnitValue": round(float(item["precio_unitario"]))
            }
            for item in items
        ],
        "payments": [
            {
                "paymentTypeId": 1,
                "amount":        round(float(total)),
                "recordDate":    ""
            }
        ]
    }

    data = json.dumps(body).encode('utf-8')
    req  = urllib.request.Request(
        _BSALE_API_URL,
        data=data,
        method='POST',
        headers={
            'access_token': token,
            'Content-Type': 'application/json',
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode('utf-8'))

        folio   = resp.get('number') or resp.get('folio')
        pdf_url = resp.get('urlPdf') or resp.get('dynamicLink', '')

        return {
            "ok":      True,
            "folio":   folio,
            "pdf_url": pdf_url,
            "numero":  f"B-{folio}" if folio else None,
            "error":   None
        }
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        return {
            "ok":      False,
            "folio":   None,
            "pdf_url": None,
            "numero":  None,
            "error":   f"HTTP {e.code}: {body[:200]}"
        }
    except Exception as e:
        return {
            "ok":      False,
            "folio":   None,
            "pdf_url": None,
            "numero":  None,
            "error":   str(e)
        }
