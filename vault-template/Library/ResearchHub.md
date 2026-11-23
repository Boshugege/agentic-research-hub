---
id: "library-research-hub"
type: "library"
title: "Research Hub Space Lua Views"
---

# Research Hub Space Lua Library

This page powers the dynamic project dashboards and reverse-chronological timeline views across the Research Vault in SilverBullet v2.

```space-lua
hub = hub or {}

local function safeStr(v, fallback)
  if v == nil or v == "" then
    return fallback or ""
  end
  return tostring(v)
end

local function shortTime(ts, fallback)
  local s = safeStr(ts, fallback or "")
  if #s >= 16 then
    return string.gsub(string.sub(s, 1, 16), "T", " ")
  end
  return s
end

local function monthOf(p)
  local ts = safeStr(p.created_at, "")
  if #ts >= 7 then
    return string.sub(ts, 1, 7)
  end
  local m = string.match(safeStr(p.name, ""), "/timeline/(%d%d%d%d%-%d%d)/")
  if m then
    return m
  end
  local lm = safeStr(p.lastModified, "")
  if #lm >= 7 then
    return string.sub(lm, 1, 7)
  end
  return "未分组"
end

local function formatTags(tags)
  if type(tags) ~= "table" or #tags == 0 then
    return ""
  end
  local out = {}
  for _, t in ipairs(tags) do
    table.insert(out, "`#" .. tostring(t) .. "`")
  end
  return " · " .. table.concat(out, " ")
end

local function inferProject(p)
  if p.project and p.project ~= "" then
    return tostring(p.project)
  end
  local slug = string.match(safeStr(p.name, ""), "^projects/([^/]+)/")
  return slug
end

local function isTimelinePage(p)
  if p.type == "timeline" then
    return true
  end
  return string.match(safeStr(p.name, ""), "^projects/[^/]+/timeline/.+") ~= nil
end

local function isLiteraturePage(p)
  if p.type == "literature" then
    return true
  end
  return string.match(safeStr(p.name, ""), "^projects/[^/]+/literature/.+") ~= nil
end

local function isInboxPage(p)
  if p.type == "raw_log" then
    return true
  end
  return string.match(safeStr(p.name, ""), "^_inbox/.+") ~= nil
end

local function sortNewestFirst(items)
  table.sort(items, function(a, b)
    local ka = safeStr(a.created_at, safeStr(a.lastModified, safeStr(a.name, "")))
    local kb = safeStr(b.created_at, safeStr(b.lastModified, safeStr(b.name, "")))
    return ka > kb
  end)
end

function hub.homeDashboard()
  local pages = query[[from index.tag "page"]]
  local projectMap = {}
  local projectOrder = {}
  local allTimelines = {}
  local inboxItems = {}

  for _, p in ipairs(pages) do
    local pname = safeStr(p.name, "")
    local livingSlug = string.match(pname, "^projects/([^/]+)/LIVING_DOC$")
    if p.type == "living_doc" or livingSlug then
      local slug = safeStr(p.project, livingSlug)
      if slug ~= "" then
        if not projectMap[slug] then
          projectMap[slug] = {
            slug = slug,
            title = safeStr(p.title, slug),
            status = safeStr(p.status, "active"),
            updated_at = safeStr(p.updated_at, safeStr(p.lastModified, "")),
            timelines = {},
          }
          table.insert(projectOrder, slug)
        else
          projectMap[slug].title = safeStr(p.title, projectMap[slug].title)
          projectMap[slug].status = safeStr(p.status, projectMap[slug].status)
          projectMap[slug].updated_at = safeStr(p.updated_at, projectMap[slug].updated_at)
        end
      end
    end

    if isTimelinePage(p) then
      table.insert(allTimelines, p)
      local slug = inferProject(p)
      if slug and slug ~= "" then
        if not projectMap[slug] then
          projectMap[slug] = {
            slug = slug,
            title = slug,
            status = "active",
            updated_at = "",
            timelines = {},
          }
          table.insert(projectOrder, slug)
        end
        table.insert(projectMap[slug].timelines, p)
      end
    elseif isInboxPage(p) then
      table.insert(inboxItems, p)
    end
  end

  table.sort(projectOrder)
  sortNewestFirst(allTimelines)
  sortNewestFirst(inboxItems)

  local lines = {}
  table.insert(lines, "## 📂 项目列表（Projects）")
  table.insert(lines, "")
  if #projectOrder == 0 then
    table.insert(lines, "_当前尚无已注册项目。_")
  else
    table.insert(lines, "| 项目（时间线仪表盘） | 统一总文档 | 时间线数 | 最新时间线 | 状态 |")
    table.insert(lines, "| :--- | :--- | :---: | :--- | :---: |")
    for _, slug in ipairs(projectOrder) do
      local proj = projectMap[slug]
      sortNewestFirst(proj.timelines)
      local latestCell = "_暂无记录_"
      if #proj.timelines > 0 then
        local latest = proj.timelines[1]
        local ltitle = safeStr(latest.title, latest.name)
        latestCell = shortTime(latest.created_at, latest.lastModified) .. " · [[" .. latest.name .. "|" .. ltitle .. "]]"
      end
      table.insert(
        lines,
        "| [[" .. "projects/" .. slug .. "/INDEX|" .. proj.title .. "]] (`" .. slug .. "`)"
          .. " | [[" .. "projects/" .. slug .. "/LIVING_DOC|LIVING_DOC]]"
          .. " | " .. tostring(#proj.timelines)
          .. " | " .. latestCell
          .. " | `" .. proj.status .. "` |"
      )
    end
  end

  table.insert(lines, "")
  table.insert(lines, "## 🕒 全站近期时间线（Recent Timeline）")
  table.insert(lines, "")
  if #allTimelines == 0 then
    table.insert(lines, "_暂无时间线笔记。可按 `Ctrl-/`（Mac `Cmd-/`）运行 `Research: New Timeline Note`，或让各项目 Agent 通过 MCP/API 写入。_")
  else
    local limit = math.min(#allTimelines, 15)
    for i = 1, limit do
      local p = allTimelines[i]
      local slug = inferProject(p) or "misc"
      local title = safeStr(p.title, p.name)
      local author = safeStr(p.author, "unknown")
      table.insert(
        lines,
        "- **" .. shortTime(p.created_at, p.lastModified) .. "** · [[" .. "projects/" .. slug .. "/INDEX|" .. slug .. "]] · [[" .. p.name .. "|" .. title .. "]] *(" .. author .. ")*" .. formatTags(p.tags)
      )
    end
  end

  table.insert(lines, "")
  table.insert(lines, "## 📥 待整理缓冲池（Inbox: " .. tostring(#inboxItems) .. "）")
  table.insert(lines, "")
  if #inboxItems == 0 then
    table.insert(lines, "✅ `_inbox` 已清空，暂无待分拣的原始日志。")
  else
    local limit = math.min(#inboxItems, 10)
    for i = 1, limit do
      local p = inboxItems[i]
      local title = safeStr(p.title, p.name)
      table.insert(lines, "- **" .. shortTime(p.created_at, p.lastModified) .. "** · [[" .. p.name .. "|" .. title .. "]]")
    end
  end

  return widget.new {
    display = "block",
    markdown = table.concat(lines, "\n"),
  }
end

function hub.projectDashboard(slug)
  local pages = query[[from index.tag "page"]]
  local timelines = {}
  local literature = {}

  for _, p in ipairs(pages) do
    local pSlug = inferProject(p)
    if pSlug == slug then
      if isTimelinePage(p) then
        table.insert(timelines, p)
      elseif isLiteraturePage(p) then
        table.insert(literature, p)
      end
    end
  end

  sortNewestFirst(timelines)
  sortNewestFirst(literature)

  local lines = {}
  table.insert(lines, "**统计**：共 `" .. tostring(#timelines) .. "` 条时间线记录 · `" .. tostring(#literature) .. "` 篇文献/参考笔记")
  table.insert(lines, "")
  table.insert(lines, "## 🕒 时间线笔记（Timeline）")
  table.insert(lines, "")

  if #timelines == 0 then
    table.insert(lines, "_该项目暂无时间线记录。使用命令面板 `Research: New Timeline Note` 手动新建，或由项目 Agent 调用 `append_timeline_note` 追加。_")
  else
    local currentMonth = nil
    for _, p in ipairs(timelines) do
      local m = monthOf(p)
      if m ~= currentMonth then
        currentMonth = m
        table.insert(lines, "")
        table.insert(lines, "### " .. m)
        table.insert(lines, "")
      end
      local title = safeStr(p.title, p.name)
      local author = safeStr(p.author, "unknown")
      local status = safeStr(p.status, "draft")
      table.insert(
        lines,
        "- **" .. shortTime(p.created_at, p.lastModified) .. "** — [[" .. p.name .. "|" .. title .. "]] *(" .. author .. " · `" .. status .. "`)*" .. formatTags(p.tags)
      )
    end
  end

  if #literature > 0 then
    table.insert(lines, "")
    table.insert(lines, "## 📚 文献与参考（Literature）")
    table.insert(lines, "")
    for _, p in ipairs(literature) do
      local title = safeStr(p.title, p.name)
      table.insert(lines, "- [[" .. p.name .. "|" .. title .. "]]" .. formatTags(p.tags))
    end
  end

  return widget.new {
    display = "block",
    markdown = table.concat(lines, "\n"),
  }
end

command.define {
  name = "Research: Go Home",
  run = function()
    editor.navigate("index")
  end,
}

command.define {
  name = "Research: New Timeline Note",
  run = function()
    local cur = editor.getCurrentPage() or ""
    local defaultProj = string.match(cur, "^projects/([^/]+)/") or "misc"
    local proj = editor.prompt("Project slug (e.g. footstep-viz, agent-deception, misc):", defaultProj)
    if not proj or proj == "" then
      return
    end
    local title = editor.prompt("Timeline note title:", "")
    if not title or title == "" then
      return
    end
    local ym = os.date("%Y-%m")
    local stamp = os.date("%Y-%m-%d_%H%M")
    local isoNow = os.date("%Y-%m-%dT%H:%M:%S+08:00")
    local safe = string.gsub(title, "[^%w%.%-%_]+", "-")
    safe = string.gsub(safe, "^%-+", "")
    safe = string.gsub(safe, "%-+$", "")
    if safe == "" then
      safe = "note"
    end
    local pageName = "projects/" .. proj .. "/timeline/" .. ym .. "/" .. stamp .. "_" .. safe
    local body = "---\n"
      .. "id: \"" .. os.date("%Y%m%d-%H%M") .. "-" .. safe .. "\"\n"
      .. "project: \"" .. proj .. "\"\n"
      .. "type: \"timeline\"\n"
      .. "title: \"" .. title .. "\"\n"
      .. "created_at: \"" .. isoNow .. "\"\n"
      .. "updated_at: \"" .. isoNow .. "\"\n"
      .. "author: \"human\"\n"
      .. "status: \"draft\"\n"
      .. "tags: []\n"
      .. "---\n\n"
      .. "# " .. title .. "\n\n"
      .. "> 项目：[[projects/" .. proj .. "/INDEX|" .. proj .. "]] · [[projects/" .. proj .. "/LIVING_DOC|LIVING_DOC]]\n\n"
      .. "## Summary\n\n"
      .. "## Notes\n"
    space.writePage(pageName, body)
    editor.navigate(pageName)
  end,
}
```
