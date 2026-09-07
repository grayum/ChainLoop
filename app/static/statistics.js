
(function(){
  const wear = JSON.parse(document.getElementById("chartData").textContent).wear;
  if (wear.length) {
    const svg = document.getElementById('wearChart'), legend=document.getElementById('wearLegend');
    const W=900,H=360,L=65,R=25,T=25,B=50;
    const pts=wear.flatMap(s=>s.points);
    const maxX=Math.max(1,...pts.map(p=>p[0])), maxY=Math.max(.5,...pts.map(p=>p[1]));
    const colors=['#2f941d','#2767c5','#b06a00','#8b3f9e','#c43d32','#14828c'];
    const el=(name,attrs={})=>{const n=document.createElementNS('http://www.w3.org/2000/svg',name);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));return n};
    const sx=x=>L+x/maxX*(W-L-R), sy=y=>H-B-y/maxY*(H-T-B);
    svg.append(el('line',{x1:L,y1:H-B,x2:W-R,y2:H-B,stroke:'#8892a0'})); svg.append(el('line',{x1:L,y1:T,x2:L,y2:H-B,stroke:'#8892a0'}));
    for(let i=0;i<=5;i++){const x=maxX*i/5, px=sx(x); const t=el('text',{x:px,y:H-20,'text-anchor':'middle','font-size':'12',fill:'#667085'});t.textContent=Math.round(x)+' km';svg.append(t)}
    for(let i=0;i<=5;i++){const y=maxY*i/5, py=sy(y); const t=el('text',{x:L-10,y:py+4,'text-anchor':'end','font-size':'12',fill:'#667085'});t.textContent=y.toFixed(2)+'%';svg.append(t)}
    wear.forEach((s,idx)=>{const color=colors[idx%colors.length]; const points=s.points.map(p=>sx(p[0])+','+sy(p[1])).join(' ');svg.append(el('polyline',{points,fill:'none',stroke:color,'stroke-width':'3'}));s.points.forEach(p=>svg.append(el('circle',{cx:sx(p[0]),cy:sy(p[1]),r:4,fill:color})));const span=document.createElement('span');span.textContent=s.chain;span.className="chart-color-"+(idx%colors.length);legend.append(span)});
  }

  const wax = JSON.parse(document.getElementById("chartData").textContent).wax;
  if (wax.length) {
    const svg=document.getElementById('waxChart'),W=900,H=360,L=65,R=25,T=25,B=60; const max=Math.max(1,...wax.map(x=>x.km)); const bw=Math.max(8,(W-L-R)/wax.length*.65); const gap=(W-L-R)/wax.length;
    const el=(name,attrs={})=>{const n=document.createElementNS('http://www.w3.org/2000/svg',name);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));return n};
    svg.append(el('line',{x1:L,y1:H-B,x2:W-R,y2:H-B,stroke:'#8892a0'})); svg.append(el('line',{x1:L,y1:T,x2:L,y2:H-B,stroke:'#8892a0'}));
    wax.forEach((r,i)=>{const h=r.km/max*(H-T-B), x=L+i*gap+(gap-bw)/2, y=H-B-h;svg.append(el('rect',{x,y,width:bw,height:h,rx:3,fill:'#49c724'}));const t=el('text',{x:x+bw/2,y:H-B+17,'text-anchor':'middle','font-size':'10',fill:'#667085'});t.textContent=r.chain_code;svg.append(t)});
    for(let i=0;i<=5;i++){const v=max*i/5,py=H-B-v/max*(H-T-B);const t=el('text',{x:L-10,y:py+4,'text-anchor':'end','font-size':'12',fill:'#667085'});t.textContent=Math.round(v)+' km';svg.append(t)}
  }
})();
