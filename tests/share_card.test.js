"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const test = require("node:test");

const sourceUrl = pathToFileURL(
  path.join(__dirname, "..", "proxy_static", "src", "share-card.js")
).href

test("formatGroupedTokens groups digits with commas", async () => {
  const { formatGroupedTokens } = await import(sourceUrl)

  assert.equal(formatGroupedTokens(0), "0")
  assert.equal(formatGroupedTokens(999), "999")
  assert.equal(formatGroupedTokens(1000), "1,000")
  assert.equal(formatGroupedTokens(1234567), "1,234,567")
  assert.equal(formatGroupedTokens(Number.NaN), "0")
  assert.equal(formatGroupedTokens(-42), "0")
})

test("buildShareCardData aggregates totals, rate, and provider ranking", async () => {
  const { buildShareCardData } = await import(sourceUrl)

  const card = buildShareCardData({
    status: {
      service: "codex-local-proxy",
      display_name: "Codex 本地中转",
      brand_mark: "CX",
      providers: [
        { provider_id: "alpha", name: "Alpha 供应商" },
        { provider_id: "beta", name: "Beta 供应商" },
        { provider_id: "gamma", name: "Gamma 供应商" }
      ],
      usage: {
        total: {
          request_count: 10,
          successful_requests: 8,
          failed_requests: 2,
          input_tokens: 1_500_000,
          output_tokens: 250_000,
          total_tokens: 2_000_000,
          cached_tokens: 300_000,
          reasoning_tokens: 90_000,
          estimated_requests: 1
        },
        by_provider: {
          beta: { total_tokens: 500_000, request_count: 4 },
          alpha: { total_tokens: 1_200_000, request_count: 5 },
          gamma: { total_tokens: 300_000, request_count: 1 }
        }
      }
    },
    now: new Date("2026-09-01T08:30:00")
  })

  assert.equal(card.dateLabel, "2026/09/01")
  assert.equal(card.weekdayLabel, "周二")
  assert.equal(card.totalTokens, 2_000_000)
  assert.equal(card.inputTokens, 1_500_000)
  assert.equal(card.outputTokens, 250_000)
  assert.equal(card.cachedTokens, 300_000)
  assert.equal(card.reasoningTokens, 90_000)
  assert.equal(card.requestCount, 10)
  assert.equal(card.successfulRequests, 8)
  assert.equal(card.failedRequests, 2)
  assert.equal(card.successRate, 80)
  assert.equal(card.estimatedRequests, 1)
  assert.equal(card.hasData, true)
  assert.deepEqual(
    card.byProvider.map(entry => [entry.providerId, entry.totalTokens]),
    [["alpha", 1_200_000], ["beta", 500_000], ["gamma", 300_000]]
  )
  assert.deepEqual(
    card.byProvider.map(entry => entry.name),
    ["Alpha 供应商", "Beta 供应商", "Gamma 供应商"]
  )
  assert.equal(card.byProvider[0].tokenShare, 60)
})

test("buildShareCardData caps providers and drops empty entries", async () => {
  const { buildShareCardData } = await import(sourceUrl)

  const card = buildShareCardData({
    status: {
      usage: {
        total: { total_tokens: 400, request_count: 3 },
        by_provider: {
          a: { total_tokens: 100, request_count: 1 },
          empty: { total_tokens: 0, request_count: 0 },
          b: { total_tokens: 300, request_count: 2 },
          c: { total_tokens: 0, request_count: 9 },
          d: { total_tokens: 0, request_count: 0 },
          e: { total_tokens: 50, request_count: 1 }
        }
      }
    },
    providerLimit: 3
  })

  assert.deepEqual(
    card.byProvider.map(entry => entry.providerId),
    ["b", "a", "e"]
  )
  assert.equal(card.byProvider[0].tokenShare, 75)
})

test("buildShareCardData handles empty status with zero-safe defaults", async () => {
  const { buildShareCardData } = await import(sourceUrl)

  const card = buildShareCardData({ status: {}, now: new Date("2026-09-01T00:00:00") })

  assert.equal(card.totalTokens, 0)
  assert.equal(card.requestCount, 0)
  assert.equal(card.successRate, 0)
  assert.equal(card.failedRequests, 0)
  assert.deepEqual(card.byProvider, [])
  assert.equal(card.hasData, false)
  assert.equal(card.serviceName, "本地中转")
})

test("shareCardFileName uses compact date and png extension", async () => {
  const { shareCardFileName } = await import(sourceUrl)

  assert.equal(shareCardFileName(new Date("2026-09-01T12:00:00")), "token-card-20260901.png")
})
