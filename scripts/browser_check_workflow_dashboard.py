#!/usr/bin/env python3
"""Exercise dashboard components in Chromium against read-only analytics routes.

Requires: node_modules/.bin/vite --config scripts/fixtures/workflow-dashboard-browser/vite.config.mjs
The harness listens only on localhost:5188. No live login or writes.
"""
import asyncio
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='workflow-browser-') as temp:
    path=ROOT/'.generated/workflow-dashboard/validation.db'
    with sqlite3.connect(path) as src, sqlite3.connect(f'{temp}/bootstrap.db') as dst: src.backup(dst)
    os.environ.update(DATABASE_URL=f'sqlite:///{temp}/bootstrap.db',ENABLE_DB_MIGRATIONS='False',DATA_DIR=temp,
                      STATIC_DIR=f'{temp}/static',FRONTEND_BUILD_DIR=f'{temp}/frontend',OFFLINE_MODE='true',
                      DATABASE_ENABLE_SQLITE_WAL='False',CORS_ALLOW_ORIGIN='http://localhost',WEBUI_SECRET_KEY='test-only')
    sys.path.insert(0,str(ROOT/'backend'))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import AsyncSession,create_async_engine
    from open_webui.internal.db import get_async_session
    from open_webui.routers.experiment_analytics import router
    from open_webui.utils.auth import get_admin_user
    from playwright.sync_api import sync_playwright,expect
    engine=create_async_engine(f'sqlite+aiosqlite:///file:{path}?mode=ro&uri=true')
    async def readonly():
        async with AsyncSession(engine) as db: yield db
    app=FastAPI();app.include_router(router,prefix='/api/v1/analytics/experiments')
    app.dependency_overrides[get_async_session]=readonly
    app.dependency_overrides[get_admin_user]=lambda:SimpleNamespace(id='test-admin',role='admin')
    errors=[]
    with TestClient(app) as client, sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda error:errors.append(str(error)))
        def api(route):
            request=route.request
            url=urlsplit(request.url)
            assert request.method=='GET' or url.path.endswith('/export/sessions'), request.url
            response=client.request(request.method,url.path+('?' + url.query if url.query else ''),content=request.post_data,headers={'Content-Type':'application/json'})
            route.fulfill(status=response.status_code,body=response.content,content_type='application/json')
        page.route('**/api/v1/analytics/experiments/**',api)
        page.goto('http://127.0.0.1:5188/admin/analytics/overview')
        expect(page.get_by_text('Started runs',exact=True)).to_be_visible(timeout=30000)
        expect(page.get_by_role('navigation').get_by_role('link')).to_have_count(4)
        page.get_by_label('Data kind filter').select_option('demo')
        expect(page.get_by_text('10 runs · 10 completed · 0 active',exact=True)).to_be_visible(timeout=20000)
        page.get_by_role('navigation').get_by_role('link',name='Workflows').click()
        page.get_by_role('button',name='Open workflow').click()
        expect(page.get_by_label('Applied configuration')).to_be_visible(timeout=20000)
        expect(page.get_by_role('button',name='View step results')).to_have_count(6)
        page.get_by_role('button',name='View step results').nth(1).click()
        expect(page.get_by_role('button',name='View answers and grading').first).to_be_visible(timeout=20000)
        page.get_by_role('button',name='View answers and grading').first.click()
        expect(page.get_by_role('heading',name='Question submission',exact=True)).to_be_visible()
        expect(page.get_by_role('button',name='Save score').first).to_be_visible()
        page.get_by_role('button',name='Close',exact=True).last.click()
        page.get_by_role('button',name='View step results').nth(2).click()
        expect(page.get_by_text('Submitted ·',exact=False).first).to_be_visible(timeout=20000)
        page.get_by_role('navigation').get_by_role('link',name='Participants').click()
        expect(page.get_by_text('10 assigned participant records')).to_be_visible(timeout=20000)
        expect(page.get_by_text('Synthetic demo',exact=True)).to_have_count(10)
        page.get_by_role('button',name='Grade 10 Demo 01',exact=True).click()
        expect(page.get_by_role('heading',name='Workflow task timeline')).to_be_visible(timeout=20000)
        expect(page.get_by_text('Task conversation',exact=True)).to_have_count(3)
        page.get_by_text('Task conversation',exact=True).first.click()
        expect(page.locator('aside').get_by_text('Synthetic demo',exact=True)).to_be_visible()
        page.get_by_role('button',name='Load timeline',exact=True).click()
        expect(page.get_by_text('Sensitive tab activity',exact=True)).to_be_visible()
        with page.expect_download() as downloaded:
            page.get_by_role('button',name='Export full session (JSON)',exact=True).click()
        downloaded.value.save_as(str(ROOT/'.generated/workflow-dashboard/browser-export.json'))
        page.get_by_role('button',name='Close',exact=True).click()
        page.get_by_role('navigation').get_by_role('link',name='LLM Usage').click()
        expect(page.locator('.text-2xl').get_by_text('106',exact=True)).to_have_count(2,timeout=20000)
        page.screenshot(path=str(ROOT/'.generated/workflow-dashboard/usage-desktop.png'),full_page=True)
        page.goto('http://127.0.0.1:5188/admin/analytics/essays?data_kind=demo')
        expect(page).to_have_url('http://127.0.0.1:5188/admin/analytics/workflows?data_kind=demo')
        page.set_viewport_size({'width':390,'height':844})
        expect(page.get_by_role('button',name='Open workflow')).to_be_visible(timeout=20000)
        page.screenshot(path=str(ROOT/'.generated/workflow-dashboard/workflows-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile page overflows'
        assert not errors, errors
        browser.close()
    asyncio.run(engine.dispose())
    print('PASS: four tabs, demo filters, six workflow steps, grading inspection, essays, task conversations, sensitive telemetry, export, retired URL, desktop and mobile rendering')
