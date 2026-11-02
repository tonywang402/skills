# content.json contract

`render` validates `content.json` against `manifest.json` and writes nothing until every rule passes. Field names are exact; unknown keys (for example `purpose` or `review`) are rejected.

Everything mechanical is generated, so it never appears in this file: diff hunks, line numbers, A/M/D/R statuses, `+/-` stats, hunk/file counts, group numbers (`01`, `02`, … per domain), navigation, rail metadata, footer, storage keys, and the page's English lookup tables.

Each diff shows only hunk headers and code lines. Renames, copies, mode changes, binary changes and missing final newlines have no row of their own, so describe them in the file's panel units.

## Bilingual text `T`

`{"zh": "…", "en": "…"}` — both non-empty, one line (whitespace is collapsed), same meaning. `en` must not contain CJK characters, except inside backtick code spans: quote code, identifiers and paths as `` `docs/说明.md` ``.

The English view replaces each Chinese text by exact lookup, so one zh string must always map to the same en string across the whole file; `render` reports conflicts. Code identifiers, file paths and domain names stay unchanged in both languages.

## Page map

| JSON path | Where it appears | Pyramid level |
|---|---|---|
| `meta.title`, `meta.subtitle` | browser tab, rail subtitle (no CJK; e.g. `ROTF2OTRTU-191 · multi-deal search`) | — |
| `conclusion` | page headline | apex — one sentence |
| `behavior` | 行为变化 table: scenario / before (merge base) / after (head) | supports the apex |
| `flow` (optional) | 实现链路 cards, 2–5 steps | |
| `changes.summary` | lead sentence under 代码改动 | summarizes all domains |
| `changes.domains[].name`, `.points` | domain card title, summary chips | summarizes its groups |
| `….groups[].title`, `.summary` | numbered logic group: short title + one-sentence reason the group exists | summarizes its files |
| `….files[].role` | one-line role in the file header | summarizes its units |
| `….files[].units[]` | "Ask about this diff" panel: name, 改了什么 (green), 为什么 (red) | leaves |
| `contract` (optional) | 数据契约 table | |
| `risks` | 风险与关注点 list with level badges | |

## Schema

```jsonc
{
  "schema": "code-review-workspace/content@1",
  "meta": { "title": "Code Review · <ticket> <topic>", "subtitle": "<ticket> · <topic>" },
  "conclusion": T,
  "behavior": { "summary": T /* optional */, "rows": [{ "scenario": T, "before": T, "after": T }] },  // ≥ 1 row
  "flow": { "summary": T /* optional */, "steps": [{ "title": T, "detail": T }] },                 // optional section, 2–5 steps
  "changes": {
    "summary": T,
    "domains": [{
      "name": "Backend",            // technical domain, English, unique; "Tests" must be last
      "points": [T],                // 1–4 chips that together summarize this domain's groups, in group order
      "groups": [{
        "title": T,
        "summary": T,
        "files": [{
          "path": "<exact path from manifest.json>",
          "role": T,
          "pairedWith": "<path>", // optional: overrides the test-pairing heuristic; also a list of paths, or null
          "units": [{ "name": "<identifier as written in code>", "change": T, "reason": T }]  // ≥ 1
        }]
      }]
    }]
  },
  "contract": { "summary": T /* optional */, "rows": [{ "boundary": T, "representation": T, "constraint": T }] },  // optional section
  "risks": { "summary": T /* optional */, "items": [{ "level": "High|Medium|Low", "title": T, "detail": T }] }   // ≥ 1 item
}
```

Omit `flow` when the change has no multi-step chain and `contract` when no interface, schema, or data shape changed. Never leave a section with placeholder rows.

## Rules `render` enforces

- Every file in `manifest.json` appears exactly once; no other paths.
- A test file (`isTest`) sits in the same group as its implementation (`testOf` in the manifest, or every path in `pairedWith`), after it.
- A test whose implementation is not in the diff (`testOf: null`, or `"pairedWith": null` when the heuristic is wrong) belongs to the `Tests` domain. `Tests` holds only such files and is the last domain.
- `pairedWith` may also attach a helper (fixture, mock) to the file it supports, with the same placement rule.
- No `<TODO>` placeholders; no CJK in any `en` (outside backtick spans), `meta.*`, or domain `name`.
- No evidence citations in any text: `path:line`, `#L12`, `L10–L20`, `Evidence:` or `证据：`. Evidence stays in `ledger.md`.
- `points`: 1–4 per domain; `flow.steps`: 2–5; every group has ≥ 1 file, every file ≥ 1 unit.

Warnings do not block rendering, but fix them unless you have a reason: `conclusion.zh` longer than 90 characters (the apex should be one sentence).

## Example (abridged)

```json
{
  "schema": "code-review-workspace/content@1",
  "meta": { "title": "Code Review · ROTF2OTRTU-191 multi-deal search", "subtitle": "ROTF2OTRTU-191 · multi-deal search" },
  "conclusion": {
    "zh": "通知中心支持按一个或多个 deal ID 精确搜索，并与可分享的 deep link 双向同步",
    "en": "The notification hub supports exact search by one or more deal IDs, kept in two-way sync with shareable deep links"
  },
  "behavior": { "rows": [{
    "scenario": { "zh": "多个 deal ID", "en": "Several deal IDs" },
    "before": { "zh": "BFF 不接收 deal ID，下游 dealIds 固定为 null", "en": "The BFF accepts no deal IDs; downstream dealIds is always null" },
    "after": { "zh": "dealIds 列表保序传给 search 与 facet", "en": "The dealIds list reaches search and facet in order" }
  }]},
  "changes": {
    "summary": { "zh": "Backend 打通 dealIds 查询链路，Frontend 把搜索输入与 deep link 汇聚为同一组 chips", "en": "The backend carries dealIds end to end; the frontend unifies search input and deep links into one set of chips" },
    "domains": [{
      "name": "Backend",
      "points": [{ "zh": "定义并验证请求边界", "en": "Define and verify the request boundary" }],
      "groups": [{
        "title": { "zh": "定义并验证请求边界", "en": "Define and verify the request boundary" },
        "summary": { "zh": "先让 Controller 接收 UUID 列表，再由相邻测试证明参数保序进入服务层", "en": "The controller accepts a UUID list first; the adjacent test proves it reaches the service in order" },
        "files": [{
          "path": "backend/src/main/kotlin/com/mbag/otr_eu/offerui/api/notification/NotificationController.kt",
          "role": { "zh": "暴露可重复的 UUID dealIds 查询参数", "en": "Expose a repeatable UUID dealIds query parameter" },
          "units": [{
            "name": "NotificationController.query",
            "change": { "zh": "新增可选的 dealIds: List<UUID> 查询参数，并放入 NotificationQueryRequest。", "en": "Adds an optional dealIds: List<UUID> query parameter and puts it into NotificationQueryRequest." },
            "reason": { "zh": "让一个请求表达多个需要精确过滤的 deal；UUID 绑定在边界处拒绝非法值。", "en": "Lets one request express several deals to filter exactly; UUID binding rejects invalid values at the boundary." }
          }]
        }, {
          "path": "backend/src/test/kotlin/com/mbag/otr_eu/offerui/api/notification/NotificationControllerTest.kt",
          "role": { "zh": "验证 Controller 保序转发多个 dealIds", "en": "Verify the controller forwards several dealIds in order" },
          "units": [{
            "name": "query should pass request params to service unchanged",
            "change": { "zh": "传入两个随机 UUID，断言捕获的请求按原顺序包含完整列表。", "en": "Passes two random UUIDs and asserts the captured request keeps the full list in order." },
            "reason": { "zh": "锁定多值参数从 HTTP 到 DTO 不丢失、不重排。", "en": "Locks in that multi-value parameters are neither dropped nor reordered between HTTP and the DTO." }
          }]
        }]
      }]
    }, {
      "name": "Tests",
      "points": [{ "zh": "验证既有筛选兼容", "en": "Verify existing filters stay compatible" }],
      "groups": [{
        "title": { "zh": "验证既有筛选兼容", "en": "Verify existing filters stay compatible" },
        "summary": { "zh": "对应实现未变更；确认新增 deep-link 字段不影响 tab 行为", "en": "The implementation is unchanged; confirms the new deep-link field leaves tab behavior intact" },
        "files": [{
          "path": "frontend/src/pages/notification-hub/components/NotificationTab/NotificationTab.test.tsx",
          "role": { "zh": "验证 tab 兼容 dealIds deep-link 状态", "en": "Verify the tab tolerates dealIds deep-link state" },
          "units": [{
            "name": "renders tasks for the selected tab",
            "change": { "zh": "测试夹具的 deep-link 状态补充 dealIds: []。", "en": "Adds dealIds: [] to the deep-link fixture state." },
            "reason": { "zh": "deep-link 类型新增必填字段后，夹具需与类型保持一致。", "en": "The deep-link type gained a required field, so the fixture must match the type." }
          }]
        }]
      }]
    }]
  },
  "risks": { "items": [{
    "level": "Medium",
    "title": { "zh": "多个 ID 的匹配语义", "en": "Matching semantics for several IDs" },
    "detail": { "zh": "列表通常表示“任一匹配”；需确认下游与产品预期一致。", "en": "A list usually means match-any; confirm downstream behavior matches product intent." }
  }]}
}
```
