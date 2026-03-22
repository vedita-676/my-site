'use strict';

require('dotenv').config();

const express = require('express');
const path    = require('path');

const app = express();

// Parse JSON bodies
app.use(express.json());

// Serve all static files from this directory (index.html, styles.css, data.json, etc.)
app.use(express.static(path.join(__dirname)));

// ─── Dynamic API route ───────────────────────────────────────────
// Routes /api/:fn → ./api/:fn.js
// Clears require cache on each call so edits are picked up without restart
app.all('/api/:fn', async (req, res) => {
  try {
    const handlerPath = path.resolve(__dirname, 'api', `${req.params.fn}.js`);
    delete require.cache[require.resolve(handlerPath)];
    const handler = require(handlerPath);
    await handler(req, res);
  } catch (err) {
    if (err.code === 'MODULE_NOT_FOUND') {
      return res.status(404).json({ error: `No handler for /api/${req.params.fn}` });
    }
    console.error(err);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// ─── Start ───────────────────────────────────────────────────────
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`\nStepChange Daily Brief → http://localhost:${PORT}\n`);
});
