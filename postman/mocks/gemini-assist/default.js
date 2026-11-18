// 계약상 비스트리밍 Gemini 응답의 최소 대역. 테스트용 데이터만 사용한다.
const http = require('http');
let failures = 0;
let status = 429;
let attempts = 0;
http.createServer((req, res) => {
  let raw = '';
  req.on('data', chunk => { raw += chunk; });
  req.on('end', () => {
    res.setHeader('Content-Type', 'application/json');
    if (req.method === 'POST' && req.url === '/control/reset') {
      const data = JSON.parse(raw || '{}');
      status = data.status || 429;
      failures = data.failures || 0;
      attempts = 0;
      return res.end(JSON.stringify({ok: true}));
    }
    if (req.method === 'GET' && req.url === '/control/status') {
      return res.end(JSON.stringify({attempts}));
    }
    if (req.method === 'POST' && /\/models\/[^/]+:generateContent(?:\?.*)?$/.test(req.url)) {
      attempts += 1;
      if (attempts <= failures) {
        res.statusCode = status;
        return res.end(JSON.stringify({error: {code: status, status: 'UNAVAILABLE', message: 'SYNTHETIC_PRIVATE_MARKER'}}));
      }
      const data = {decision:'NEED_USER_INPUT', questionIntent:'DEVICE_SAFETY', factKey:'installedApp'};
      return res.end(JSON.stringify({candidates:[{content:{role:'model',parts:[{text:JSON.stringify(data)}]},finishReason:'STOP'}]}));
    }
    res.statusCode = 501;
    res.end(JSON.stringify({error: {code:501, message:'Not modelled'}}));
  });
}).listen(process.env.PORT);
