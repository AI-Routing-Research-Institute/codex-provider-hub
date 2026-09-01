const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const formatterUrl = pathToFileURL(
  path.join(__dirname, '..', 'proxy_static', 'src', 'token-format.js')
).href

test('modern Token formatter matches classic compact formatting', async () => {
  const { formatTokenCount } = await import(formatterUrl)

  assert.equal(formatTokenCount(Number.NaN), '0')
  assert.equal(formatTokenCount(0), '0')
  assert.equal(formatTokenCount(999), '999')
  assert.equal(formatTokenCount(1_000), '1K')
  assert.equal(formatTokenCount(1_250), '1.25K')
  assert.equal(formatTokenCount(1_000_000), '1M')
  assert.equal(formatTokenCount(1_250_000), '1.25M')
  assert.equal(formatTokenCount(1_000_000_000), '1B')
  assert.equal(formatTokenCount(1_250_000_000), '1.25B')
})
