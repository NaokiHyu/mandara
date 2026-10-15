// UI のスモークテスト：ヘッドレスの Chrome（DevTools Protocol）で画面を開き、
//   - ブラウザのコンソールのエラー・例外、読み込みの失敗が1つでもあれば失敗
//   - 画面ごとに、見えているべきものが見えているかを確かめる。操作（act）があれば、その後の状態（after）も
// 画像だけでは「空の地図」が正常に見えてしまう（Worker の読み込み失敗を見逃した）ので、コンソールで判定する。
// 使い方: node scripts/ui_smoke.mjs BASE_URL [OUT_DIR]（OUT_DIR があれば画面の画像を書く）
// 環境変数 CHROME で Chrome の場所を変えられる。
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base, outDir] = process.argv.slice(2);
if (!base) throw new Error("使い方: node scripts/ui_smoke.mjs BASE_URL [OUT_DIR]");
const CHROME = process.env.CHROME ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const profile = mkdtempSync(join(tmpdir(), "mandara-smoke-"));

const CASES = [
  { name: "graph", query: "?ontology=gold:adventureworks&table=tbl_sales_salesorderdetail",
    expect: "document.querySelector('.graph canvas') !== null && document.querySelector('.detail h1')?.textContent === 'salesorderdetail'" },
  { name: "list", query: "?ontology=gold:adventureworks&view=list",
    expect: "document.querySelectorAll('.contexts .table').length === 14" },
  { name: "map", query: "?ontology=gold:adventureworks&table=tbl_person_address&view=map",
    expect: "document.querySelector('.map canvas') !== null && /person\\.address\\.spatiallocation · 19,614 points/.test(document.querySelector('.map-status')?.textContent ?? '')" },
  { name: "unknown view", query: "?ontology=gold:adventureworks&view=nope",
    expect: "/表示 nope は無い/.test(document.body.textContent ?? '') && document.querySelector('.graph canvas') !== null" },
  // レビュー（使い捨ての作業領域。scripts/check.sh が --reviewer smoke で立てる）：答えると書かれ、問いが1つ減る
  { name: "review answer", query: "?ontology=work:adventureworks&view=review",
    expect: "document.querySelector('.card .question') !== null && !document.querySelector('.banner.warn')",
    act: "window.__pending = Number(document.querySelector('.review-queue .count').textContent.replace(/,/g, '')); " +
         "window.dispatchEvent(new KeyboardEvent('keydown', { key: 'n' }))",
    after: "Number(document.querySelector('.review-queue .count')?.textContent.replace(/,/g, '')) === window.__pending - 1" },
  // 一括承認：一覧を見てから承認すると、候補が無くなる
  { name: "review bulk", query: "?ontology=work:adventureworks&view=review",
    expect: "document.querySelector('.banner.bulk button') !== null",
    act: "document.querySelector('.banner.bulk button').click()",
    then: "document.querySelector('.bulk-panel footer .approve')",
    thenAct: "document.querySelector('.bulk-panel footer .approve').click()",
    after: "document.querySelector('.banner.bulk') === null && document.querySelector('.card .question') !== null" },
  // 承認した結果が、オントロジーの画面（表の詳細）に出る
  { name: "approved in ontology", query: "?ontology=work:adventureworks&view=list",
    expect: "document.querySelectorAll('.contexts .table').length > 0",
    act: "document.querySelector('.contexts .table').click()",
    after: "document.querySelectorAll('.detail tr.status-approved').length > 0" },
];

// DevTools のポートは Chrome に選ばせ（0）、プロファイルの DevToolsActivePort から読む（使用中のポートと衝突しない）
const chrome = spawn(CHROME, ["--headless=new", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
  "--window-size=1500,860", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });

async function until(what, probe, ms = 15000) {
  const deadline = Date.now() + ms;
  for (;;) {
    const value = await probe().catch(() => undefined);
    if (value !== undefined) return value;
    if (Date.now() > deadline) throw new Error(`待ちきれない: ${what}`);
    await sleep(200);
  }
}

let failures = 0;
try {
  const port = await until("Chrome の DevTools のポート", async () => {
    const file = join(profile, "DevToolsActivePort");
    return existsSync(file) ? readFileSync(file, "utf-8").split("\n")[0] : undefined;
  });
  const page = await until("Chrome のページ", async () => {
    const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
    return targets.find((t) => t.type === "page");
  });
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.addEventListener("open", r); ws.addEventListener("error", j); });
  let id = 0;
  const pending = new Map();
  const problems = [];
  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++id;
    const timer = setTimeout(() => { pending.delete(i); rej(new Error(`DevTools が応答しない: ${method}`)); }, 15000);
    pending.set(i, (v) => { clearTimeout(timer); res(v); });
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result ?? m.error); pending.delete(m.id); }
    if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
      problems.push(`console.error: ${m.params.args.map((a) => a.value ?? a.description).join(" ")}`);
    }
    if (m.method === "Runtime.exceptionThrown") {
      problems.push(`exception: ${m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text}`);
    }
    if (m.method === "Log.entryAdded" && m.params.entry.level === "error") {
      problems.push(`log: ${m.params.entry.text} ${m.params.entry.url ?? ""}`);
    }
  });
  await send("Runtime.enable"); await send("Log.enable"); await send("Page.enable");

  for (const c of CASES) {
    problems.length = 0;
    await send("Page.navigate", { url: base + c.query });
    let ok = false;
    for (let i = 0; i < 40 && !ok; i++) {
      await sleep(250);
      const r = await send("Runtime.evaluate", { expression: c.expect, returnByValue: true });
      ok = r?.result?.value === true;
    }
    if (ok && c.act) {
      await send("Runtime.evaluate", { expression: c.act });
      if (c.then) { // 次の操作の対象が現れるまで待ってから操作する（時間で待たない）
        ok = false;
        for (let i = 0; i < 40 && !ok; i++) {
          await sleep(250);
          const r = await send("Runtime.evaluate", { expression: `(${c.then}) !== null`, returnByValue: true });
          ok = r?.result?.value === true;
        }
        if (ok) await send("Runtime.evaluate", { expression: c.thenAct });
      }
      ok = false;
      for (let i = 0; i < 40 && !ok; i++) {
        await sleep(250);
        const r = await send("Runtime.evaluate", { expression: c.after, returnByValue: true });
        ok = r?.result?.value === true;
      }
    }
    await sleep(500); // 描画の後に出るエラーも拾う
    if (outDir) {
      const shot = await send("Page.captureScreenshot", { format: "png" });
      writeFileSync(join(outDir, `smoke-${c.name}.png`), Buffer.from(shot.data, "base64"));
    }
    const verdict = ok && problems.length === 0;
    if (!verdict) failures++;
    console.log(`${verdict ? "ok  " : "FAIL"} ${c.name}${ok ? "" : "（見えるべきものが見えない）"}`);
    for (const p of problems) console.log(`     ${p}`);
  }
  ws.close();
} finally {
  chrome.kill();
}
process.exit(failures === 0 ? 0 : 1);
