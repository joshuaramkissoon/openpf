/*
 * OpenPF view runtime — injected into every sandboxed view iframe.
 *
 * Plain ES2020, no dependencies, no network. The view talks to the app ONLY via
 * postMessage to the parent, which whitelists methods and does the fetching.
 * Exposes `window.openpf` (documented in backend/app/views_sdk/VIEW_SDK.md).
 *
 * The parent prepends `window.__OPENPF_INIT__ = {currency, privacy, state, theme}`
 * before this script.
 */
;(function () {
  'use strict'

  var INIT = window.__OPENPF_INIT__ || {}
  var parentWin = window.parent
  var SCRAMBLE = 1.11 * 1.23
  var MARK = '\u2063' // invisible separator: tags money text for privacy blur
  var nextId = 1
  var pending = {}

  // ── bridge ──────────────────────────────────────────────────────────────
  function post(msg) {
    msg.__openpf = 1
    try {
      parentWin.postMessage(msg, '*')
    } catch (_) {
      /* parent gone */
    }
  }

  function call(method, args, timeoutMs) {
    return new Promise(function (resolve, reject) {
      var id = nextId++
      var timer = setTimeout(function () {
        delete pending[id]
        reject(new Error(method + ' timed out'))
      }, timeoutMs || 45000)
      pending[id] = { resolve: resolve, reject: reject, timer: timer }
      post({ id: id, method: method, args: args || [] })
    })
  }

  window.addEventListener('message', function (event) {
    if (event.source !== parentWin) return
    var msg = event.data
    if (!msg || msg.__openpf !== 1) return
    if (msg.event === 'theme' && msg.vars) {
      applyTheme(msg.vars)
      return
    }
    var p = pending[msg.id]
    if (!p) return
    delete pending[msg.id]
    clearTimeout(p.timer)
    if (msg.ok) p.resolve(msg.result)
    else p.reject(new Error(msg.error || 'request failed'))
  })

  function applyTheme(vars) {
    var root = document.documentElement
    for (var k in vars) {
      if (Object.prototype.hasOwnProperty.call(vars, k) && /^--[a-z0-9-]+$/.test(k)) root.style.setProperty(k, vars[k])
    }
  }
  if (INIT.theme) applyTheme(INIT.theme)

  // ── formatting ──────────────────────────────────────────────────────────
  var currency = INIT.currency === 'USD' ? 'USD' : 'GBP'
  var privacy = INIT.privacy === 'blur' || INIT.privacy === 'scramble' ? INIT.privacy : 'off'

  function isNum(v) {
    return typeof v === 'number' && isFinite(v)
  }

  function mark(s) {
    return privacy === 'blur' ? MARK + s + MARK : s
  }

  function money(v, opts) {
    opts = opts || {}
    if (!isNum(v)) return '—'
    var cur = opts.currency || currency
    var value = privacy === 'scramble' ? v * SCRAMBLE : v
    var abs = Math.abs(value)
    var out
    try {
      if (opts.compact && abs >= 10000) {
        out = new Intl.NumberFormat('en-GB', { style: 'currency', currency: cur, notation: 'compact', maximumFractionDigits: 1 }).format(abs)
      } else {
        var d = opts.decimals != null ? opts.decimals : abs >= 100000 ? 0 : 2
        out = new Intl.NumberFormat('en-GB', { style: 'currency', currency: cur, minimumFractionDigits: d, maximumFractionDigits: d }).format(abs)
      }
    } catch (_) {
      out = abs.toFixed(2) + ' ' + cur
    }
    var sign = value < 0 ? '−' : opts.signed && value > 0 ? '+' : ''
    return mark(sign + out)
  }

  function pct(v, opts) {
    opts = opts || {}
    if (!isNum(v)) return '—'
    var d = opts.decimals != null ? opts.decimals : Math.abs(v) < 0.1 ? 2 : 1
    var s = Math.abs(v * 100).toFixed(d) + '%'
    return (v < 0 ? '−' : opts.signed && v > 0 ? '+' : '') + s
  }

  function signedPct(v, opts) {
    return pct(v, Object.assign({}, opts || {}, { signed: true }))
  }

  function num(v, opts) {
    opts = opts || {}
    if (!isNum(v)) return '—'
    if (opts.compact) return new Intl.NumberFormat('en-GB', { notation: 'compact', maximumFractionDigits: 1 }).format(v)
    var d = opts.decimals != null ? opts.decimals : Math.abs(v) >= 100 ? 0 : 2
    return new Intl.NumberFormat('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d }).format(v)
  }

  function toDate(v) {
    if (v instanceof Date) return v
    if (typeof v === 'number') return new Date(v)
    if (typeof v === 'string') {
      if (/^\d{4}-\d{2}$/.test(v)) return new Date(v + '-01T00:00:00')
      if (/^\d{4}-\d{2}-\d{2}$/.test(v)) return new Date(v + 'T00:00:00')
      return new Date(v)
    }
    return new Date(NaN)
  }

  function date(v, style) {
    var d = toDate(v)
    if (isNaN(d.getTime())) return '—'
    style = style || 'short'
    if (style === 'month') return d.toLocaleDateString('en-GB', { month: 'short', year: 'numeric' })
    if (style === 'monthShort') return d.toLocaleDateString('en-GB', { month: 'short' })
    if (style === 'long') return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })
    if (style === 'relative') {
      var days = Math.round((Date.now() - d.getTime()) / 86400000)
      if (days === 0) return 'today'
      if (days === 1) return 'yesterday'
      if (days > 1 && days < 30) return days + ' days ago'
      if (days < 0 && days > -30) return 'in ' + -days + ' days'
    }
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' })
  }

  function fmtWith(format) {
    if (typeof format === 'function') return format
    if (format === 'money') return function (v) { return money(v) }
    if (format === 'moneyCompact') return function (v) { return money(v, { compact: true }) }
    if (format === 'pct') return function (v) { return pct(v) }
    if (format === 'signedPct') return function (v) { return signedPct(v) }
    return function (v) { return num(v) }
  }

  // ── DOM helpers ─────────────────────────────────────────────────────────
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    })
  }

  function $(target) {
    if (typeof target === 'string') {
      var el = document.querySelector(target)
      if (!el) throw new Error('No element matches ' + target)
      return el
    }
    return target
  }

  function h(tag, attrs) {
    var el = document.createElement(tag)
    if (attrs) {
      for (var k in attrs) {
        var v = attrs[k]
        if (v == null || v === false) continue
        if (k === 'class' || k === 'className') el.className = v
        else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v)
        else if (k.slice(0, 2) === 'on' && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v)
        else if (k === 'html') el.innerHTML = v
        else el.setAttribute(k, v === true ? '' : v)
      }
    }
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i])
    return el
  }

  function append(el, child) {
    if (child == null || child === false) return
    if (Array.isArray(child)) return child.forEach(function (c) { append(el, c) })
    el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)))
  }

  var SVGNS = 'http://www.w3.org/2000/svg'
  function s(tag, attrs) {
    var el = document.createElementNS(SVGNS, tag)
    for (var k in attrs || {}) if (attrs[k] != null) el.setAttribute(k, attrs[k])
    return el
  }

  // ── tooltip ─────────────────────────────────────────────────────────────
  var tip
  function showTip(html, x, y) {
    if (!tip) {
      tip = h('div', { class: 'op-tip' })
      document.body.appendChild(tip)
    }
    tip.innerHTML = html
    tip.style.display = 'block'
    var w = tip.offsetWidth
    var vw = document.documentElement.clientWidth
    var left = x + 14
    if (left + w > vw - 8) left = x - w - 14
    tip.style.left = Math.max(8, left) + 'px'
    tip.style.top = Math.max(8, y - 12) + 'px'
  }
  function hideTip() {
    if (tip) tip.style.display = 'none'
  }

  // ── charts ──────────────────────────────────────────────────────────────
  var PALETTE = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)', 'var(--chart-6)', 'var(--chart-7)', 'var(--chart-8)']
  var uid = 0

  function niceTicks(min, max, count) {
    if (min === max) {
      var pad = Math.abs(min) * 0.1 || 1
      min -= pad
      max += pad
    }
    var span = max - min
    var step = Math.pow(10, Math.floor(Math.log10(span / count)))
    var err = (count * step) / span
    if (err <= 0.15) step *= 10
    else if (err <= 0.35) step *= 5
    else if (err <= 0.75) step *= 2
    var lo = Math.floor(min / step) * step
    var hi = Math.ceil(max / step) * step
    var ticks = []
    for (var v = lo; v <= hi + step / 2; v += step) ticks.push(Math.abs(v) < step / 1e6 ? 0 : v)
    return ticks
  }

  function axisFmt(format) {
    if (format === 'money' || format === 'moneyCompact') return function (v) { return money(v, { compact: true, decimals: 0 }) }
    if (format === 'pct' || format === 'signedPct') return function (v) { return pct(v, { decimals: 0 }) }
    if (typeof format === 'function') return format
    return function (v) { return num(v, { compact: Math.abs(v) >= 10000, decimals: Math.abs(v) < 10 ? 1 : 0 }) }
  }

  // Mount a chart: renders now and re-renders on container resize.
  function mount(target, render) {
    var el = $(target)
    el.classList.add('op-chart')
    var lastW = -1
    function draw() {
      var w = Math.max(120, Math.floor(el.clientWidth))
      if (w === lastW) return
      lastW = w
      el.innerHTML = ''
      render(el, w)
    }
    draw()
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(draw).observe(el)
    return { redraw: function () { lastW = -1; draw() } }
  }

  function legend(items) {
    return h(
      'div',
      { class: 'op-legend' },
      items.map(function (it) {
        return h('span', { class: 'op-legend-item' }, h('i', { style: { background: it.color } }), it.name)
      }),
    )
  }

  function lineLike(target, opts, filled) {
    var series = opts.series || [{ name: opts.name || '', points: opts.data || [] }]
    series = series.map(function (sr, i) {
      return {
        name: sr.name || '',
        color: sr.color || PALETTE[i % PALETTE.length],
        points: (sr.points || []).map(function (p) { return { x: toX(p.x), y: p.y, raw: p } }).filter(function (p) { return isNum(p.y) && isNum(p.x) }),
      }
    })
    var fmt = fmtWith(opts.format)
    var yFmt = axisFmt(opts.format)
    var height = opts.height || 240
    return mount(target, function (el, width) {
      var all = []
      series.forEach(function (sr) { all = all.concat(sr.points) })
      if (!all.length) return el.appendChild(h('div', { class: 'op-empty' }, 'No data yet'))
      var ys = all.map(function (p) { return p.y })
      var minY = Math.min.apply(null, ys)
      var maxY = Math.max.apply(null, ys)
      if (opts.zero) {
        minY = Math.min(0, minY)
        maxY = Math.max(0, maxY)
      }
      var ticks = niceTicks(minY, maxY, 4)
      var y0 = ticks[0]
      var y1 = ticks[ticks.length - 1]
      var xs = all.map(function (p) { return p.x })
      var x0 = Math.min.apply(null, xs)
      var x1 = Math.max.apply(null, xs)
      if (x0 === x1) x1 = x0 + 1
      var labelW = Math.max.apply(null, ticks.map(function (t) { return yFmt(t).replace(MARK, '').length })) * 6.4 + 10
      var m = { l: labelW, r: 8, t: 8, b: 24 }
      var iw = width - m.l - m.r
      var ih = height - m.t - m.b
      var X = function (v) { return m.l + ((v - x0) / (x1 - x0)) * iw }
      var Y = function (v) { return m.t + ih - ((v - y0) / (y1 - y0)) * ih }
      var svg = s('svg', { width: width, height: height, viewBox: '0 0 ' + width + ' ' + height, class: 'op-svg' })
      ticks.forEach(function (t) {
        svg.appendChild(s('line', { x1: m.l, x2: width - m.r, y1: Y(t), y2: Y(t), class: t === 0 && y0 < 0 ? 'op-zero' : 'op-grid-line' }))
        var tx = s('text', { x: m.l - 8, y: Y(t) + 4, 'text-anchor': 'end', class: 'op-axis' })
        tx.textContent = yFmt(t)
        svg.appendChild(tx)
      })
      var isDate = series.some(function (sr) { return sr.points.some(function (p) { return p.raw && typeof p.raw.x !== 'number' }) })
      var xticks = Math.max(2, Math.min(6, Math.floor(iw / 90)))
      for (var i = 0; i <= xticks; i++) {
        var xv = x0 + ((x1 - x0) * i) / xticks
        var lab = s('text', { x: X(xv), y: height - 6, 'text-anchor': i === 0 ? 'start' : i === xticks ? 'end' : 'middle', class: 'op-axis' })
        lab.textContent = isDate ? date(xv, x1 - x0 > 300 * 86400000 ? 'month' : 'short') : num(xv)
        svg.appendChild(lab)
      }
      series.forEach(function (sr, si) {
        if (!sr.points.length) return
        var d = sr.points.map(function (p, j) { return (j ? 'L' : 'M') + X(p.x).toFixed(1) + ' ' + Y(p.y).toFixed(1) }).join(' ')
        if (filled) {
          var gid = 'opg' + ++uid
          var grad = s('linearGradient', { id: gid, x1: 0, x2: 0, y1: 0, y2: 1 })
          var st1 = s('stop', { offset: '0%' })
          st1.setAttribute('style', 'stop-color:' + sr.color + ';stop-opacity:0.28')
          var st2 = s('stop', { offset: '100%' })
          st2.setAttribute('style', 'stop-color:' + sr.color + ';stop-opacity:0')
          grad.appendChild(st1)
          grad.appendChild(st2)
          var defs = s('defs')
          defs.appendChild(grad)
          svg.appendChild(defs)
          var base = Y(Math.max(y0, Math.min(0, y1)))
          var area = s('path', { d: d + ' L' + X(sr.points[sr.points.length - 1].x) + ' ' + base + ' L' + X(sr.points[0].x) + ' ' + base + ' Z' })
          area.setAttribute('style', 'fill:url(#' + gid + ')')
          svg.appendChild(area)
        }
        var path = s('path', { d: d, fill: 'none', 'stroke-width': si === 0 ? 1.75 : 1.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' })
        path.setAttribute('style', 'stroke:' + sr.color + (sr.dashed ? ';stroke-dasharray:4 4' : ''))
        svg.appendChild(path)
      })
      var cross = s('line', { y1: m.t, y2: m.t + ih, class: 'op-cross', visibility: 'hidden' })
      svg.appendChild(cross)
      var dots = series.map(function (sr) {
        var c = s('circle', { r: 3.5, visibility: 'hidden' })
        c.setAttribute('style', 'fill:' + sr.color + ';stroke:var(--card);stroke-width:2')
        svg.appendChild(c)
        return c
      })
      var hit = s('rect', { x: m.l, y: m.t, width: iw, height: ih, fill: 'transparent' })
      svg.appendChild(hit)
      hit.addEventListener('mousemove', function (ev) {
        var r = svg.getBoundingClientRect()
        var xv = x0 + ((ev.clientX - r.left - m.l) / iw) * (x1 - x0)
        var rows = []
        var cx = null
        series.forEach(function (sr, si) {
          var best = null
          sr.points.forEach(function (p) { if (!best || Math.abs(p.x - xv) < Math.abs(best.x - xv)) best = p })
          if (!best) return dots[si].setAttribute('visibility', 'hidden')
          cx = cx == null ? best.x : cx
          dots[si].setAttribute('cx', X(best.x))
          dots[si].setAttribute('cy', Y(best.y))
          dots[si].setAttribute('visibility', 'visible')
          rows.push('<div class="op-tip-row"><i style="background:' + sr.color + '"></i>' + (sr.name ? esc(sr.name) + ' ' : '') + '<b>' + esc(fmt(best.y, best.raw)) + '</b></div>')
        })
        if (cx == null) return
        cross.setAttribute('x1', X(cx))
        cross.setAttribute('x2', X(cx))
        cross.setAttribute('visibility', 'visible')
        showTip('<div class="op-tip-title">' + esc(isDate ? date(cx, 'long') : num(cx)) + '</div>' + rows.join(''), ev.clientX, ev.clientY)
      })
      hit.addEventListener('mouseleave', function () {
        cross.setAttribute('visibility', 'hidden')
        dots.forEach(function (d) { d.setAttribute('visibility', 'hidden') })
        hideTip()
      })
      el.appendChild(svg)
      if (series.length > 1 && opts.legend !== false) el.appendChild(legend(series))
    })
  }

  function toX(x) {
    if (isNum(x)) return x
    var d = toDate(x)
    return d.getTime()
  }

  function bar(target, opts) {
    var data = (opts.data || []).filter(function (d) { return isNum(d.value) })
    var fmt = fmtWith(opts.format)
    var yFmt = axisFmt(opts.format)
    if (opts.horizontal) return hbar(target, data, fmt, opts)
    var height = opts.height || 240
    return mount(target, function (el, width) {
      if (!data.length) return el.appendChild(h('div', { class: 'op-empty' }, opts.empty || 'No data yet'))
      var vals = data.map(function (d) { return d.value })
      var ticks = niceTicks(Math.min(0, Math.min.apply(null, vals)), Math.max(0, Math.max.apply(null, vals)), 4)
      var y0 = ticks[0]
      var y1 = ticks[ticks.length - 1]
      var labelW = Math.max.apply(null, ticks.map(function (t) { return yFmt(t).replace(MARK, '').length })) * 6.4 + 10
      var m = { l: labelW, r: 4, t: 8, b: 24 }
      var iw = width - m.l - m.r
      var ih = height - m.t - m.b
      var Y = function (v) { return m.t + ih - ((v - y0) / (y1 - y0)) * ih }
      var bw = iw / data.length
      var gap = Math.min(10, bw * 0.28)
      var svg = s('svg', { width: width, height: height, viewBox: '0 0 ' + width + ' ' + height, class: 'op-svg' })
      ticks.forEach(function (t) {
        svg.appendChild(s('line', { x1: m.l, x2: width - m.r, y1: Y(t), y2: Y(t), class: t === 0 ? 'op-zero' : 'op-grid-line' }))
        var tx = s('text', { x: m.l - 8, y: Y(t) + 4, 'text-anchor': 'end', class: 'op-axis' })
        tx.textContent = yFmt(t)
        svg.appendChild(tx)
      })
      var every = Math.ceil(data.length / Math.max(1, Math.floor(iw / 44)))
      data.forEach(function (d, i) {
        var x = m.l + i * bw + gap / 2
        var top = Y(Math.max(0, d.value))
        var hgt = Math.max(1, Math.abs(Y(d.value) - Y(0)))
        var color = d.color || opts.color || (d.value < 0 ? 'var(--negative)' : 'var(--chart-1)')
        var r = s('rect', { x: x, y: top, width: Math.max(1, bw - gap), height: hgt, rx: Math.min(3, (bw - gap) / 3), class: 'op-bar' })
        r.setAttribute('style', 'fill:' + color + (d.muted ? ';opacity:0.45' : ''))
        r.addEventListener('mousemove', function (ev) {
          showTip('<div class="op-tip-title">' + esc(d.label) + '</div><div class="op-tip-row"><b>' + esc(fmt(d.value, d)) + '</b></div>' + (d.note ? '<div class="op-tip-note">' + esc(d.note) + '</div>' : ''), ev.clientX, ev.clientY)
        })
        r.addEventListener('mouseleave', hideTip)
        if (opts.onClick) {
          r.style.cursor = 'pointer'
          r.addEventListener('click', function () { opts.onClick(d) })
        }
        svg.appendChild(r)
        if (i % every === 0) {
          var lab = s('text', { x: x + (bw - gap) / 2, y: height - 6, 'text-anchor': 'middle', class: 'op-axis' })
          lab.textContent = d.short || d.label
          svg.appendChild(lab)
        }
      })
      el.appendChild(svg)
    })
  }

  function hbar(target, data, fmt, opts) {
    var el = $(target)
    el.classList.add('op-chart')
    el.innerHTML = ''
    if (!data.length) return el.appendChild(h('div', { class: 'op-empty' }, opts.empty || 'No data yet'))
    var max = Math.max.apply(null, data.map(function (d) { return Math.abs(d.value) })) || 1
    var list = h('div', { class: 'op-hbars' })
    data.forEach(function (d) {
      var color = d.color || opts.color || (d.value < 0 ? 'var(--negative)' : 'var(--chart-1)')
      var row = h(
        'div',
        { class: 'op-hbar' + (opts.onClick ? ' op-clickable' : ''), onclick: opts.onClick ? function () { opts.onClick(d) } : null },
        h('span', { class: 'op-hbar-label' }, d.label),
        h('span', { class: 'op-hbar-track' }, h('span', { class: 'op-hbar-fill', style: { width: (Math.abs(d.value) / max) * 100 + '%', background: color } })),
        h('span', { class: 'op-hbar-value' }, fmt(d.value, d)),
      )
      list.appendChild(row)
    })
    el.appendChild(list)
    return { redraw: function () {} }
  }

  function stackedBar(target, opts) {
    var labels = opts.labels || []
    var series = (opts.series || []).map(function (sr, i) { return { name: sr.name, values: sr.values || [], color: sr.color || PALETTE[i % PALETTE.length] } })
    var fmt = fmtWith(opts.format)
    var yFmt = axisFmt(opts.format)
    var height = opts.height || 240
    return mount(target, function (el, width) {
      if (!labels.length) return el.appendChild(h('div', { class: 'op-empty' }, 'No data yet'))
      var totals = labels.map(function (_, i) { return series.reduce(function (a, sr) { return a + Math.max(0, sr.values[i] || 0) }, 0) })
      var ticks = niceTicks(0, Math.max.apply(null, totals.concat([0])), 4)
      var y1 = ticks[ticks.length - 1] || 1
      var labelW = Math.max.apply(null, ticks.map(function (t) { return yFmt(t).replace(MARK, '').length })) * 6.4 + 10
      var m = { l: labelW, r: 4, t: 8, b: 24 }
      var iw = width - m.l - m.r
      var ih = height - m.t - m.b
      var Y = function (v) { return m.t + ih - (v / y1) * ih }
      var bw = iw / labels.length
      var gap = Math.min(10, bw * 0.28)
      var svg = s('svg', { width: width, height: height, viewBox: '0 0 ' + width + ' ' + height, class: 'op-svg' })
      ticks.forEach(function (t) {
        svg.appendChild(s('line', { x1: m.l, x2: width - m.r, y1: Y(t), y2: Y(t), class: t === 0 ? 'op-zero' : 'op-grid-line' }))
        var tx = s('text', { x: m.l - 8, y: Y(t) + 4, 'text-anchor': 'end', class: 'op-axis' })
        tx.textContent = yFmt(t)
        svg.appendChild(tx)
      })
      var every = Math.ceil(labels.length / Math.max(1, Math.floor(iw / 44)))
      labels.forEach(function (label, i) {
        var x = m.l + i * bw + gap / 2
        var acc = 0
        series.forEach(function (sr) {
          var v = Math.max(0, sr.values[i] || 0)
          if (!v) return
          var r = s('rect', { x: x, y: Y(acc + v), width: Math.max(1, bw - gap), height: Math.max(0.5, Y(acc) - Y(acc + v)), class: 'op-bar' })
          r.setAttribute('style', 'fill:' + sr.color)
          acc += v
          svg.appendChild(r)
        })
        var hit = s('rect', { x: m.l + i * bw, y: m.t, width: bw, height: ih, fill: 'transparent' })
        hit.addEventListener('mousemove', function (ev) {
          var rows = series.filter(function (sr) { return sr.values[i] }).map(function (sr) {
            return '<div class="op-tip-row"><i style="background:' + sr.color + '"></i>' + esc(sr.name) + ' <b>' + esc(fmt(sr.values[i])) + '</b></div>'
          })
          showTip('<div class="op-tip-title">' + esc(label) + ' · ' + esc(fmt(totals[i])) + '</div>' + rows.join(''), ev.clientX, ev.clientY)
        })
        hit.addEventListener('mouseleave', hideTip)
        svg.appendChild(hit)
        if (i % every === 0) {
          var lab = s('text', { x: x + (bw - gap) / 2, y: height - 6, 'text-anchor': 'middle', class: 'op-axis' })
          lab.textContent = label
          svg.appendChild(lab)
        }
      })
      el.appendChild(svg)
      if (opts.legend !== false) el.appendChild(legend(series))
    })
  }

  function donut(target, opts) {
    var data = (opts.data || []).filter(function (d) { return isNum(d.value) && d.value > 0 })
    var fmt = fmtWith(opts.format)
    var el = $(target)
    el.classList.add('op-chart')
    el.innerHTML = ''
    if (!data.length) return el.appendChild(h('div', { class: 'op-empty' }, 'No data yet'))
    var size = opts.size || 180
    var thick = opts.thickness || Math.round(size * 0.13)
    var total = data.reduce(function (a, d) { return a + d.value }, 0)
    var R = size / 2
    var r = R - thick / 2
    var svg = s('svg', { width: size, height: size, viewBox: '0 0 ' + size + ' ' + size, class: 'op-svg' })
    var C = 2 * Math.PI * r
    var acc = 0
    data.forEach(function (d, i) {
      var color = d.color || PALETTE[i % PALETTE.length]
      d._color = color
      var len = (d.value / total) * C
      var c = s('circle', { cx: R, cy: R, r: r, fill: 'none', 'stroke-width': thick, 'stroke-dasharray': Math.max(0, len - 1.5) + ' ' + (C - Math.max(0, len - 1.5)), 'stroke-dashoffset': -acc, transform: 'rotate(-90 ' + R + ' ' + R + ')' })
      c.setAttribute('style', 'stroke:' + color)
      c.addEventListener('mousemove', function (ev) {
        showTip('<div class="op-tip-title">' + esc(d.label) + '</div><div class="op-tip-row"><b>' + esc(fmt(d.value, d)) + '</b> · ' + pct(d.value / total) + '</div>', ev.clientX, ev.clientY)
      })
      c.addEventListener('mouseleave', hideTip)
      svg.appendChild(c)
      acc += len
    })
    if (opts.centerValue != null || opts.centerLabel) {
      var t1 = s('text', { x: R, y: R + (opts.centerLabel ? 2 : 6), 'text-anchor': 'middle', class: 'op-donut-value' })
      t1.textContent = opts.centerValue != null ? opts.centerValue : ''
      svg.appendChild(t1)
      if (opts.centerLabel) {
        var t2 = s('text', { x: R, y: R + 20, 'text-anchor': 'middle', class: 'op-axis' })
        t2.textContent = opts.centerLabel
        svg.appendChild(t2)
      }
    }
    var wrap = h('div', { class: 'op-donut' }, h('div', { class: 'op-donut-ring' }, svg))
    if (opts.legend !== false) {
      wrap.appendChild(
        h(
          'div',
          { class: 'op-donut-legend' },
          data.map(function (d) {
            return h('div', { class: 'op-donut-row' }, h('i', { style: { background: d._color } }), h('span', { class: 'op-donut-label' }, d.label), h('span', { class: 'op-donut-num' }, fmt(d.value, d)), h('span', { class: 'op-donut-pct' }, pct(d.value / total, { decimals: 1 })))
          }),
        ),
      )
    }
    el.appendChild(wrap)
    return { redraw: function () {} }
  }

  function sparkline(target, opts) {
    var values = (Array.isArray(opts) ? opts : opts.values || []).filter(isNum)
    opts = Array.isArray(opts) ? {} : opts
    var height = opts.height || 32
    return mount(target, function (el, width) {
      if (values.length < 2) return
      var min = Math.min.apply(null, values)
      var max = Math.max.apply(null, values)
      if (min === max) max = min + 1
      var color = opts.color || (values[values.length - 1] >= values[0] ? 'var(--positive)' : 'var(--negative)')
      var d = values.map(function (v, i) { return (i ? 'L' : 'M') + ((i / (values.length - 1)) * (width - 2) + 1).toFixed(1) + ' ' + (height - 2 - ((v - min) / (max - min)) * (height - 4)).toFixed(1) }).join(' ')
      var svg = s('svg', { width: width, height: height, viewBox: '0 0 ' + width + ' ' + height, class: 'op-svg' })
      var p = s('path', { d: d, fill: 'none', 'stroke-width': 1.5, 'stroke-linejoin': 'round' })
      p.setAttribute('style', 'stroke:' + color)
      svg.appendChild(p)
      el.appendChild(svg)
    })
  }

  function heatmap(target, opts) {
    var rows = opts.rows || []
    var cols = opts.cols || []
    var values = opts.values || []
    var fmt = fmtWith(opts.format)
    return mount(target, function (el, width) {
      if (!rows.length || !cols.length) return el.appendChild(h('div', { class: 'op-empty' }, 'No data yet'))
      var flat = []
      values.forEach(function (r) { (r || []).forEach(function (v) { if (isNum(v)) flat.push(v) }) })
      var maxAbs = Math.max.apply(null, flat.map(Math.abs).concat([1e-9]))
      var min = Math.min.apply(null, flat.concat([0]))
      var max = Math.max.apply(null, flat.concat([0]))
      var diverging = opts.diverging != null ? opts.diverging : min < 0
      var labelW = Math.min(140, Math.max.apply(null, rows.map(function (r) { return String(r).length })) * 7 + 12)
      var cw = (width - labelW) / cols.length
      var ch = opts.cellHeight || 30
      var height = rows.length * ch + 22
      var svg = s('svg', { width: width, height: height, viewBox: '0 0 ' + width + ' ' + height, class: 'op-svg' })
      cols.forEach(function (c, j) {
        if (cw < 26 && j % Math.ceil(26 / cw)) return
        var t = s('text', { x: labelW + j * cw + cw / 2, y: 14, 'text-anchor': 'middle', class: 'op-axis' })
        t.textContent = c
        svg.appendChild(t)
      })
      rows.forEach(function (r, i) {
        var t = s('text', { x: labelW - 8, y: 22 + i * ch + ch / 2 + 4, 'text-anchor': 'end', class: 'op-axis' })
        t.textContent = r
        svg.appendChild(t)
        cols.forEach(function (c, j) {
          var v = (values[i] || [])[j]
          var rect = s('rect', { x: labelW + j * cw + 1, y: 22 + i * ch + 1, width: Math.max(1, cw - 2), height: ch - 2, rx: 3 })
          var style
          if (!isNum(v)) style = 'fill:var(--muted);opacity:0.4'
          else if (diverging) style = 'fill:' + (v >= 0 ? 'var(--positive)' : 'var(--negative)') + ';opacity:' + (0.12 + 0.78 * (Math.abs(v) / maxAbs)).toFixed(3)
          else style = 'fill:var(--chart-1);opacity:' + (0.1 + 0.85 * ((v - min) / (max - min || 1))).toFixed(3)
          rect.setAttribute('style', style)
          rect.addEventListener('mousemove', function (ev) {
            showTip('<div class="op-tip-title">' + esc(r) + ' · ' + esc(c) + '</div><div class="op-tip-row"><b>' + esc(isNum(v) ? fmt(v) : '—') + '</b></div>', ev.clientX, ev.clientY)
          })
          rect.addEventListener('mouseleave', hideTip)
          svg.appendChild(rect)
          if (isNum(v) && cw >= 46 && opts.showValues !== false) {
            var vt = s('text', { x: labelW + j * cw + cw / 2, y: 22 + i * ch + ch / 2 + 4, 'text-anchor': 'middle', class: 'op-cell' })
            vt.textContent = fmt(v)
            svg.appendChild(vt)
          }
        })
      })
      el.appendChild(svg)
    })
  }

  // ── table ───────────────────────────────────────────────────────────────
  function table(target, opts) {
    var el = $(target)
    var columns = opts.columns || []
    var rows = (opts.rows || []).slice()
    var sortKey = opts.sort ? opts.sort.key : null
    var sortDir = opts.sort ? opts.sort.dir || 'desc' : 'desc'
    function cell(col, row) {
      var v = row[col.key]
      if (col.render) {
        var out = col.render(v, row)
        return out instanceof Node ? out : h('span', { html: String(out == null ? '' : out) })
      }
      if (col.ticker) return h('button', { class: 'op-ticker', onclick: function () { openTicker(v) } }, String(v))
      var text = col.format ? fmtWith(col.format)(v, row) : v == null ? '—' : String(v)
      var cls = ''
      if (col.tone && isNum(v)) cls = v > 0 ? 'op-positive' : v < 0 ? 'op-negative' : ''
      return h('span', { class: cls }, text)
    }
    function draw() {
      if (sortKey) {
        rows.sort(function (a, b) {
          var x = a[sortKey]
          var y = b[sortKey]
          var r = isNum(x) && isNum(y) ? x - y : String(x == null ? '' : x).localeCompare(String(y == null ? '' : y))
          return sortDir === 'asc' ? r : -r
        })
      }
      el.innerHTML = ''
      if (!rows.length) return el.appendChild(h('div', { class: 'op-empty' }, opts.empty || 'Nothing to show yet'))
      var numeric = function (c) { return c.align ? c.align === 'right' : !!c.format }
      var thead = h(
        'thead',
        null,
        h(
          'tr',
          null,
          columns.map(function (c) {
            return h(
              'th',
              {
                class: (numeric(c) ? 'num ' : '') + (opts.sortable !== false ? 'op-sortable' : ''),
                onclick: opts.sortable !== false ? function () { sortDir = sortKey === c.key && sortDir === 'desc' ? 'asc' : 'desc'; sortKey = c.key; draw() } : null,
              },
              c.label,
              sortKey === c.key ? (sortDir === 'desc' ? ' ↓' : ' ↑') : '',
            )
          }),
        ),
      )
      var tbody = h(
        'tbody',
        null,
        rows.map(function (row) {
          return h('tr', null, columns.map(function (c) { return h('td', { class: numeric(c) ? 'num' : '' }, cell(c, row)) }))
        }),
      )
      el.appendChild(h('div', { class: 'op-table-wrap' }, h('table', { class: 'op-table' }, thead, tbody)))
    }
    draw()
    return { update: function (next) { rows = next.slice(); draw() } }
  }

  // ── state, actions, data ────────────────────────────────────────────────
  var stateData = INIT.state && typeof INIT.state === 'object' ? INIT.state : {}
  var stateTimer = null
  var state = {
    get: function (key, fallback) {
      return Object.prototype.hasOwnProperty.call(stateData, key) ? JSON.parse(JSON.stringify(stateData[key])) : fallback
    },
    all: function () {
      return JSON.parse(JSON.stringify(stateData))
    },
    set: function (key, value) {
      stateData[key] = value === undefined ? null : JSON.parse(JSON.stringify(value))
      clearTimeout(stateTimer)
      return new Promise(function (resolve, reject) {
        stateTimer = setTimeout(function () { call('state.set', [stateData]).then(resolve, reject) }, 250)
      })
    },
  }

  function data(source, params) {
    return call('data', [String(source), params || {}])
  }
  function ask(prompt) {
    post({ method: 'ask', args: [String(prompt).slice(0, 4000)] })
  }
  function openTicker(t) {
    post({ method: 'openTicker', args: [String(t)] })
  }
  function navigate(path) {
    post({ method: 'navigate', args: [String(path)] })
  }

  // ── errors ──────────────────────────────────────────────────────────────
  var errorShown = false
  function reportError(err) {
    var message = (err && (err.message || err.reason || String(err))) || 'Unknown error'
    var stack = err && err.stack ? String(err.stack).split('\n').slice(0, 6).join('\n') : ''
    post({ method: 'error', args: [{ message: String(message).slice(0, 600), stack: stack.slice(0, 1500) }] })
    if (errorShown) return
    errorShown = true
    var panel = h(
      'div',
      { class: 'op-error' },
      h('div', { class: 'op-error-title' }, 'This view hit an error'),
      h('div', { class: 'op-error-msg' }, String(message).slice(0, 300)),
      h(
        'div',
        { class: 'op-row' },
        h('button', { class: 'op-btn op-btn-primary', onclick: function () { post({ method: 'fix', args: [{ message: String(message).slice(0, 600), stack: stack }] }) } }, 'Fix it'),
        h('button', { class: 'op-btn', onclick: function () { post({ method: 'reload', args: [] }) } }, 'Reload'),
      ),
    )
    document.body.insertBefore(panel, document.body.firstChild)
  }
  window.addEventListener('error', function (e) { reportError(e.error || e.message) })
  window.addEventListener('unhandledrejection', function (e) { reportError(e.reason) })

  function ready(fn) {
    var run = function () {
      try {
        var r = fn()
        if (r && typeof r.then === 'function') r.then(null, reportError)
      } catch (e) {
        reportError(e)
      }
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run)
    else run()
  }

  // ── privacy blur: blur any text carrying the money marker ───────────────
  if (privacy === 'blur') {
    var scheduled = false
    var scan = function () {
      scheduled = false
      var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
      var n
      while ((n = walker.nextNode())) {
        if (n.nodeValue && n.nodeValue.indexOf(MARK) !== -1 && n.parentNode && n.parentNode.classList) n.parentNode.classList.add('op-blur')
      }
    }
    new MutationObserver(function () {
      if (!scheduled) {
        scheduled = true
        requestAnimationFrame(scan)
      }
    }).observe(document.documentElement, { subtree: true, childList: true, characterData: true })
  }

  // ── auto-resize ─────────────────────────────────────────────────────────
  var lastH = 0
  function sendHeight() {
    var hgt = Math.ceil(document.documentElement.scrollHeight)
    if (Math.abs(hgt - lastH) < 2) return
    lastH = hgt
    post({ method: 'resize', args: [hgt] })
  }
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(sendHeight).observe(document.documentElement)
  window.addEventListener('load', sendHeight)

  var fmt = { money: money, pct: pct, signedPct: signedPct, num: num, date: date }
  window.openpf = Object.freeze({
    version: 1,
    currency: currency,
    privacy: privacy,
    ready: ready,
    data: data,
    state: Object.freeze(state),
    ask: ask,
    openTicker: openTicker,
    navigate: navigate,
    fmt: Object.freeze(fmt),
    money: money,
    pct: pct,
    signedPct: signedPct,
    num: num,
    date: date,
    h: h,
    esc: esc,
    colors: PALETTE.slice(),
    charts: Object.freeze({ line: function (t, o) { return lineLike(t, o, false) }, area: function (t, o) { return lineLike(t, o, true) }, bar: bar, stackedBar: stackedBar, donut: donut, sparkline: sparkline, heatmap: heatmap }),
    table: table,
    tone: function (v) { return isNum(v) ? (v > 0 ? 'op-positive' : v < 0 ? 'op-negative' : 'op-muted') : 'op-muted' },
  })
})()
