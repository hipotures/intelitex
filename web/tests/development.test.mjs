import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import test from 'node:test'
import { chromium } from 'playwright'

test('real Vite proxy preserves same-origin mutations and streams SSE without buffering', {timeout:30000}, async t => {
 const fixture=spawn('uv',['run','--group','dev','python','tests/web_fixture_server.py','--port','8780'],{cwd:'..',stdio:['pipe','pipe','pipe']})
 t.after(()=>fixture.stdin.end('quit\n'))
 let failure='';fixture.stderr.on('data',v=>failure+=v)
 await new Promise((resolve,reject)=>{createInterface({input:fixture.stdout}).on('line',line=>{try{if(JSON.parse(line).url)resolve()}catch{}});fixture.once('exit',()=>reject(Error(failure)))})
 const vite=spawn(process.execPath,['node_modules/vite/bin/vite.js'],{stdio:['ignore','pipe','pipe']})
 t.after(()=>vite.kill('SIGTERM'))
 await new Promise((resolve,reject)=>{vite.stdout.on('data',v=>{if(String(v).includes('127.0.0.1:5173'))resolve()});vite.once('exit',reject)})
 const browser=await chromium.launch();t.after(()=>browser.close())
 const page=await browser.newPage();const errors=[]
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())})
 await page.goto('http://127.0.0.1:5173/work')
 await page.getByRole('status').filter({hasText:'Live'}).waitFor()
 await page.locator('[data-source-id="second-book"]').click()
 await page.getByRole('heading',{name:'Prepare this workspace'}).waitFor()
 const source=await page.evaluate(async()=>await(await fetch('/api/library')).json())
 assert.ok(source.sources.find(s=>s.source_id==='second-book').workspace_id)
 const forbidden=await page.request.get('http://127.0.0.1:5173/api/health',{headers:{Origin:'https://evil.invalid'}})
 assert.equal(forbidden.status(),403)
 assert.deepEqual(errors,[])
})
