// Scroll-down presentation, rendered from /static/slides.json.
//
// Every slide is a flat list of typed blocks; editing a slide's copy or
// reordering blocks only requires editing slides.json — no JSX change.
// Recognised block types are documented in the JSON's _meta section.

const { useEffect: _useEffect, useState: _useState } = React;
const _rootPath = (window.HARDAD && window.HARDAD.rootPath) || "";

function Eyebrow({ text }) {
  return <div className="pres-eyebrow">{text}</div>;
}

function Title({ text, html, hero }) {
  const Tag = hero ? "h1" : "h2";
  if (html) return <Tag className="pres-title" dangerouslySetInnerHTML={{ __html: html }} />;
  return <Tag className="pres-title">{text}</Tag>;
}

function Lede({ text, html }) {
  if (html) return <p className="pres-lede" dangerouslySetInnerHTML={{ __html: html }} />;
  return <p className="pres-lede">{text}</p>;
}

function Body({ text, html }) {
  if (html) return <p className="pres-body" dangerouslySetInnerHTML={{ __html: html }} />;
  return <p className="pres-body">{text}</p>;
}

function Pills({ items }) {
  return (
    <div className="pill-row">
      {(items || []).map((it, i) => (
        <span key={i} className={"pill" + (it.variant ? " " + it.variant : "")}>{it.text}</span>
      ))}
    </div>
  );
}

function Stats({ items }) {
  return (
    <div className="pres-grid">
      {(items || []).map((s, i) => (
        <div className="stat" key={i}>
          <div className="stat-k">{s.k}</div>
          <div className="stat-v" style={s.vSmall ? { fontSize: 18 } : undefined}>{s.v}</div>
          {s.d && <div className={"stat-d" + (s.dClass === "neg" ? " neg" : "")}>{s.d}</div>}
        </div>
      ))}
    </div>
  );
}

function Cell({ cell }) {
  const cls = cell.class ? cell.class : "";
  if (cell.html) return <td className={cls} dangerouslySetInnerHTML={{ __html: cell.html }} />;
  return <td className={cls}>{cell.text}</td>;
}

function KvTable({ headers, rows, maxWidth }) {
  const style = maxWidth ? { maxWidth } : undefined;
  return (
    <table className="kv-table" style={style}>
      {headers && headers.length > 0 && (
        <thead>
          <tr>{headers.map((h, i) => <th key={i}>{h}</th>)}</tr>
        </thead>
      )}
      <tbody>
        {(rows || []).map((row, i) => (
          <tr key={i}>
            {row.map((cell, j) => <Cell key={j} cell={cell} />)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Arch({ items }) {
  return (
    <div className="arch">
      {(items || []).map((it, i) => {
        if (it.tag === "arrow") {
          return <div key={i} className="arch-arrow">{it.text}</div>;
        }
        const style = it.indent ? { marginLeft: it.indent } : undefined;
        return (
          <div key={i} className="arch-node" style={style}>
            <span className="n-name">{it.name}</span>
            {it.label && <span className="n-tag">{it.label}</span>}
          </div>
        );
      })}
    </div>
  );
}

function ScLayers({ items }) {
  const colorMap = {
    err:   { bg: "rgba(240,71,71,0.2)",  fg: "var(--err)" },
    "l-c2":{ bg: "rgba(234,179,8,0.2)",  fg: "var(--l-c2)" },
    ok:    { bg: "rgba(61,214,140,0.2)", fg: "var(--ok)" },
  };
  return (
    <div style={{ marginTop: 24 }}>
      {(items || []).map((it, i) => {
        const c = colorMap[it.color] || { bg: "var(--bg-panel)", fg: "var(--ink)" };
        return (
          <div key={i} className="sc-layer">
            <div className="sc-icon" style={{ background: c.bg, color: c.fg }}>{it.level}</div>
            <div className="sc-body">
              <div className="sc-title">{it.title}</div>
              {it.desc && <div className="sc-desc">{it.desc}</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Callout({ text, html }) {
  if (html) return <div className="callout" dangerouslySetInnerHTML={{ __html: html }} />;
  return <div className="callout">{text}</div>;
}

function Image({ src, alt, caption, maxWidth }) {
  const style = maxWidth ? { maxWidth } : undefined;
  return (
    <figure className="pres-image" style={style}>
      <img src={_rootPath + "/static/" + src} alt={alt || ""} loading="lazy" />
      {caption && <figcaption>{caption}</figcaption>}
    </figure>
  );
}

function SbomCompare({ items }) {
  return (
    <div className="sbom-compare">
      {(items || []).map((it, i) => (
        <div key={i} className={"sbom-frame" + (it.variant ? " " + it.variant : "")}>
          <div className="sbom-label">{it.label}</div>
          <div className="sbom-viewport">
            <img src={_rootPath + "/static/" + it.src} alt={it.label} loading="lazy" />
          </div>
        </div>
      ))}
    </div>
  );
}

function Block({ block }) {
  switch (block.type) {
    case "eyebrow":   return <Eyebrow {...block} />;
    case "title":     return <Title   {...block} />;
    case "lede":      return <Lede    {...block} />;
    case "body":      return <Body    {...block} />;
    case "pills":     return <Pills   {...block} />;
    case "stats":     return <Stats   {...block} />;
    case "table":     return <KvTable {...block} />;
    case "arch":      return <Arch    {...block} />;
    case "sc-layers": return <ScLayers {...block} />;
    case "callout":   return <Callout {...block} />;
    case "image":     return <Image   {...block} />;
    case "sbom-compare": return <SbomCompare {...block} />;
    default:
      return <div style={{color:"var(--err)"}}>Unknown block type: {block.type}</div>;
  }
}

function Slide({ slide }) {
  return (
    <div className="pres-slide" data-screen-label={slide.label || slide.id}>
      {(slide.blocks || []).map((b, i) => <Block key={i} block={b} />)}
    </div>
  );
}

function Presentation() {
  const [doc, setDoc] = _useState(null);
  const [err, setErr] = _useState(null);

  _useEffect(() => {
    fetch(_rootPath + "/static/slides.json", { credentials: "same-origin" })
      .then(r => r.json())
      .then(d => setDoc(d))
      .catch(e => setErr(e.message || "Kunde inte ladda slides.json"));
  }, []);

  if (err) return (
    <section className="presentation">
      <div className="pres-slide"><div className="pres-body" style={{color:"var(--err)"}}>{err}</div></div>
    </section>
  );
  if (!doc) return (
    <section className="presentation">
      <div className="pres-slide"><div className="pres-body" style={{color:"var(--ink-mute)"}}>Laddar presentationen…</div></div>
    </section>
  );

  return (
    <section className="presentation">
      {(doc.slides || []).map(s => <Slide key={s.id} slide={s} />)}
    </section>
  );
}

window.Presentation = Presentation;
