import json
from pathlib import Path
from typing import Optional

from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from lib.db import (
    RAW_TO_REVIEWED,
    approve_record,
    get_company_summary,
    get_db_connection,
    init_db,
    list_records_with_status,
    reject_record,
    preview_merge,
    execute_merge,
)

app = FastAPI(title="OSINT Review Dashboard")

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/")
def root():
    return RedirectResponse(
        url="/companies", status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


@app.get("/companies")
def list_companies(request: Request):
    conn = get_db_connection()
    try:
        init_db(conn)
        companies = get_company_summary(conn)
    finally:
        conn.close()

    for company in companies:
        company["total_raw"] = sum(
            val
            for key, val in company.items()
            if key.startswith("raw_") and isinstance(val, int)
        )
        company["total_reviewed"] = sum(
            val
            for key, val in company.items()
            if key.startswith("reviewed_") and isinstance(val, int)
        )

    return templates.TemplateResponse(
        request=request,
        name="companies.html",
        context={
            "companies": companies,
        },
    )


@app.get("/companies/{slug}")
def review_company(request: Request, slug: str):
    conn = get_db_connection()
    tables_data = {}
    try:
        for raw_table in RAW_TO_REVIEWED.keys():
            records = list_records_with_status(conn, raw_table, slug)
            parsed_records = []
            for r in records:
                r_dict = dict(r)
                # Parse JSON string so we can pretty-print it in the template
                parsed_data = json.loads(r_dict["data"]) if r_dict["data"] else {}
                r_dict["json_string"] = json.dumps(parsed_data, indent=2)
                parsed_records.append(r_dict)
            tables_data[raw_table] = parsed_records
    finally:
        conn.close()

    return templates.TemplateResponse(
        request=request,
        name="review.html",
        context={
            "slug": slug,
            "tables_data": tables_data,
        },
    )


@app.post("/companies/{slug}/approve")
def approve_company_record(
    slug: str,
    raw_table: str = Form(...),
    record_key: str = Form(...),
    edited_data: Optional[str] = Form(None),
):
    parsed_data = None
    if edited_data and edited_data.strip():
        try:
            parsed_data = json.loads(edited_data)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON format provided in edited_data.",
            )

    conn = get_db_connection()
    try:
        approve_record(conn, raw_table, slug, record_key, parsed_data)
    finally:
        conn.close()

    return RedirectResponse(
        url=f"/companies/{slug}", status_code=status.HTTP_303_SEE_OTHER
    )


@app.post("/companies/{slug}/reject")
def reject_company_record(
    slug: str,
    raw_table: str = Form(...),
    record_key: str = Form(...),
):
    conn = get_db_connection()
    try:
        reject_record(conn, raw_table, slug, record_key)
    finally:
        conn.close()

    return RedirectResponse(
        url=f"/companies/{slug}", status_code=status.HTTP_303_SEE_OTHER
    )


@app.get("/merge")
def merge_form(request: Request):
    return templates.TemplateResponse(request=request, name="merge.html", context={})


@app.post("/merge/preview")
def merge_preview_route(
    request: Request, source_slug: str = Form(...), target_slug: str = Form(...)
):
    conn = get_db_connection()
    try:
        breakdown = preview_merge(conn, source_slug, target_slug)
    finally:
        conn.close()

    return templates.TemplateResponse(
        request=request,
        name="merge_preview.html",
        context={
            "source_slug": source_slug,
            "target_slug": target_slug,
            "breakdown": breakdown,
            "is_preview": True,
        },
    )


@app.post("/merge/execute")
def merge_execute_route(
    request: Request, source_slug: str = Form(...), target_slug: str = Form(...)
):
    conn = get_db_connection()
    try:
        breakdown = execute_merge(conn, source_slug, target_slug)
    finally:
        conn.close()

    return templates.TemplateResponse(
        request=request,
        name="merge_preview.html",
        context={
            "source_slug": source_slug,
            "target_slug": target_slug,
            "breakdown": breakdown,
            "is_preview": False,
        },
    )
