"""Optional smoke test: requires Playwright and an installed Chromium.

Set CHROMIUM_PATH when the executable is not on PATH. Browser fetch requests are
bridged to the real local HTTP handler so this test also runs without browser
network access. Only synthetic English-language fixtures are used.
"""
from pathlib import Path
import json, tempfile, threading, sys, shutil, os
import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bookpipe.review import ReviewServer, ReviewRepository
from bookpipe.util import atomic_json, read_json
from playwright.sync_api import sync_playwright, expect

def term(i,category,confidence,reviewed=False,notes=''):
    return {'id':f'T{i:06}', 'source':f'Entity {i}', 'aliases':[], 'category':category, 'select':1, 'custom':'',
            'reviewed':reviewed, 'user_notes':notes, 'meaning_notes':[{'text':'A test description.','confidence':confidence}],
            'candidates':[{'number':1,'text':f'Name {i}','confidence':confidence,'reasons':['Test candidate']}],
            'evidence':[{'block_id':f'B{i}','chapter_id':'ch1','excerpt':f'Entity {i} appeared in the room.','order':i} for _ in range(15 if i==1 else 1)]}

with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary)
    data={'format_version':1,'confirmed':False,'terms':[
        term(1,'people','medium'),term(2,'people','low',True),term(3,'people','high'),
        term(4,'place','medium',True),term(5,'technology','high'),term(6,'place','high',True,'Inspect the context')
    ]}
    atomic_json(root/'terms.review.json',data)
    server=ReviewServer(('127.0.0.1',0),ReviewRepository(root/'terms.review.json'))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH') or shutil.which('chromium') or shutil.which('google-chrome'),headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1600,'height':1000})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            # Render locally without browser network access. Exercise the real HTTP
            # handler through a Python bridge for fetch(), not a fake data model.
            client=httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}')
            def bridge(url,options):
                r=client.request(options.get('method','GET'),url,content=options.get('body'),headers={'Content-Type':'application/json'})
                return {'status':r.status_code,'text':r.text}
            page.expose_function('backendFetch',bridge)
            html=client.get('/').text
            filters=client.get('/assets/review_filters.js').text
            html=html.replace('<script src="/assets/review_filters.js"></script>', '<script>'+filters+'</script>')
            bridge_js="<script>window.fetch=async(url,options={})=>{const r=await window.backendFetch(url,options);return {ok:r.status<400,status:r.status,json:async()=>JSON.parse(r.text)}};</script>"
            html=html.replace('<script>\nconst labels',bridge_js+'<script>\nconst labels')
            page.set_content(html)
            expect(page.locator('[data-cat=all]')).to_have_text('All 6')
            expect(page.locator('[data-cat=people]')).to_have_text('People 3')
            expect(page.locator('.translation-placeholder').first).to_contain_text('Not translated yet')
            y1=page.locator('#saveNext').bounding_box()['y']
            # Different evidence lengths must not move the action button.
            page.locator('[data-id=T000003]').click()
            y2=page.locator('#saveNext').bounding_box()['y']
            assert abs(y1-y2)<1,(y1,y2)
            button=page.locator('#saveNext').bounding_box();kbd=page.locator('#saveNext .kbd').bounding_box()
            assert abs((button['y']+button['height']/2)-(kbd['y']+kbd['height']/2))<1
            page.locator('[data-filter=uncertain]').click()
            expect(page.locator('[data-cat=all]')).to_have_text('All 3')
            expect(page.locator('[data-cat=people]')).to_have_text('People 2')
            page.locator('[data-cat=people]').click()
            expect(page.locator('[data-filter=all]')).to_have_text('All 3')
            expect(page.locator('[data-filter=uncertain]')).to_have_text('Uncertain 2')
            expect(page.locator('#detail h2')).to_have_text('Entity 1')
            page.locator('#saveNext').click()
            expect(page.locator('[data-cat=people]')).to_have_text('✓ People 2')
            expect(page.locator('[data-cat=people]')).to_have_class('chip active complete ')
            expect(page.locator('#saveNext')).to_be_disabled()
            # Switch back to All: one high-confidence person is still pending.
            page.locator('[data-filter=all]').click()
            expect(page.locator('[data-cat=people]')).to_have_text('People 3')
            assert 'complete' not in page.locator('[data-cat=people]').get_attribute('class')
            page.locator('[data-id=T000003]').click()
            page.locator('#custom').fill('Custom name')
            page.locator('#userNotes').fill('Check this after translation.')
            # No wait for autosave: the filter action must flush both edits.
            page.locator('[data-filter=notes]').click()
            expect(page.locator('[data-cat=people]')).to_have_text('People 1')
            expect(page.locator('[data-filter=notes]')).to_have_text('Notes 1')
            page.on('dialog',lambda d:d.accept())
            page.locator('#bulkBtn').click()
            expect(page.locator('[data-cat=people]')).to_have_text('✓ People 1')
            stored=read_json(root/'terms.review.json')
            assert stored['terms'][2]['custom']=='Custom name'
            assert stored['terms'][2]['user_notes']=='Check this after translation.'
            assert stored['terms'][2]['reviewed'] is True
            assert stored['terms'][4]['reviewed'] is False
            assert stored['confirmed'] is False
            assert list(root.glob('history/review_before_bulk_*.json'))
            page.locator('[data-filter=unreviewed]').click()
            expect(page.locator('[data-cat=people]')).to_have_text('✓ People 0')
            assert 'complete' in page.locator('[data-cat=people]').get_attribute('class')
            expect(page.locator('#detail .empty')).to_be_visible()
            page.locator('[data-cat=all]').click()
            expect(page.locator('#detail h2')).to_have_text('Entity 5')
            page.locator('#search').fill('nonexistent')
            expect(page.locator('[data-cat=all]')).to_have_text('All 0')
            assert 'complete' not in page.locator('[data-cat=all]').get_attribute('class')
            assert not errors,errors
            browser.close()
        print('Browser checks passed: scoped single counts, green completion state, empty distinction, fixed footer, shortcut alignment, draft flush, notes, bulk preservation.')
    finally:
        server.shutdown();server.server_close();thread.join()
