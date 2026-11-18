(function () {
  "use strict";
  const data = JSON.parse(document.getElementById("chartData").textContent);
  const W = 900, H = 360, L = 65, R = 25, T = 25;
  function el(name, attrs = {}) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  }
  function label(svg, value, attrs) {
    const node = el("text", {class: "chart-label", "font-size": 12, ...attrs});
    node.textContent = value;
    svg.append(node);
  }
  function axes(svg, bottom) {
    svg.append(el("line", {class: "chart-axis", x1: L, y1: H - bottom, x2: W - R, y2: H - bottom}));
    svg.append(el("line", {class: "chart-axis", x1: L, y1: T, x2: L, y2: H - bottom}));
  }
  function grid(svg, y) {
    svg.append(el("line", {class: "chart-grid", x1: L, y1: y, x2: W - R, y2: y}));
  }
  if (data.wear.length) {
    const svg = document.getElementById("wearChart");
    const legend = document.getElementById("wearLegend");
    const bottom = 50;
    const points = data.wear.flatMap(series => series.points);
    const maxX = Math.max(1, ...points.map(point => point[0]));
    const maxY = Math.max(.5, ...points.map(point => point[1]));
    const sx = x => L + x / maxX * (W - L - R);
    const sy = y => H - bottom - y / maxY * (H - T - bottom);
    for (let i = 0; i <= 5; i++) {
      const x = maxX * i / 5, y = maxY * i / 5;
      grid(svg, sy(y));
      label(svg, Math.round(x) + " km", {x: sx(x), y: H - 20, "text-anchor": "middle"});
      label(svg, y.toFixed(2) + "%", {x: L - 10, y: sy(y) + 4, "text-anchor": "end"});
    }
    axes(svg, bottom);
    data.wear.forEach((series, index) => {
      const className = "chart-color-" + (index % 6);
      // CSS variables recolor the existing SVG immediately on a theme change.
      svg.append(el("polyline", {
        class: className, points: series.points.map(point => sx(point[0]) + "," + sy(point[1])).join(" "),
        fill: "none", stroke: "currentColor", "stroke-width": 3,
        "stroke-dasharray": ["none", "8 3", "3 3", "10 3 2 3", "6 5", "2 4"][index % 6],
      }));
      series.points.forEach(point => svg.append(el("circle", {class: className, cx: sx(point[0]), cy: sy(point[1]), r: 4, fill: "currentColor"})));
      const span = document.createElement("span");
      span.textContent = series.chain;
      span.className = className;
      legend.append(span);
    });
  }
  if (data.wax.length) {
    const svg = document.getElementById("waxChart");
    const bottom = 60;
    const max = Math.max(1, ...data.wax.map(record => record.km));
    const gap = (W - L - R) / data.wax.length;
    const width = Math.max(1, gap * .65);
    for (let i = 0; i <= 5; i++) {
      const value = max * i / 5, y = H - bottom - value / max * (H - T - bottom);
      grid(svg, y);
      label(svg, Math.round(value) + " km", {x: L - 10, y: y + 4, "text-anchor": "end"});
    }
    axes(svg, bottom);
    data.wax.forEach((record, index) => {
      const height = record.km / max * (H - T - bottom);
      const x = L + index * gap + (gap - width) / 2;
      const bar = el("rect", {class: "chart-wax", x, y: H - bottom - height, width, height, rx: 3});
      const title = el("title");
      title.textContent = record.chain_code + ": " + record.km.toFixed(1) + " km";
      bar.append(title);
      svg.append(bar);
      if (index % Math.ceil(data.wax.length / 10) === 0) {
        label(svg, record.chain_code, {x: x + width / 2, y: H - bottom + 17, "text-anchor": "middle", "font-size": 10});
      }
    });
  }
})();
