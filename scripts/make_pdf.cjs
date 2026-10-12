// 주간 리포트 PDF 발행본 생성: node scripts/make_pdf.cjs <사이트 URL> <출력 경로>
// 예) node scripts/make_pdf.cjs http://localhost:8765/#report reports/2026-10-12_No001.pdf
const { chromium } = require('playwright');
(async () => {
  const [url, out] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1100, height: 1400 } });
  await p.emulateMedia({ media: 'print' });
  await p.goto(url, { waitUntil: 'networkidle' });
  await p.waitForFunction(() => document.querySelector('#sections table'), null, { timeout: 30000 });
  await p.pdf({ path: out, format: 'A4', printBackground: true, preferCSSPageSize: true, scale: 0.9,
    displayHeaderFooter: true, headerTemplate: '<span></span>',
    footerTemplate: '<div style="font-size:8px;width:100%;text-align:center;color:#666">주간 철강 및 철강원료 동향 · <span class="pageNumber"></span>/<span class="totalPages"></span></div>' });
  await b.close();
  console.log('saved', out);
})();
