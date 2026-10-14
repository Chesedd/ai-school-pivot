import {useEffect, useMemo, useRef, useState} from "react";
import {ApiError} from "./api";
import {readScanPageContent, type ScanPage} from "./paperScanApi";
import type {FindingRegion, ReviewFinding} from "./paperReviewApi";
import {regionStyle} from "./paperReviewState";

export type ViewerFinding = Omit<ReviewFinding, "source_check_finding_id"> & {source_check_finding_id?: string | null};

export function ScanPageViewer<F extends ViewerFinding = ReviewFinding>({page, number, findings = [], selectedFinding, onFinding, pickPoint, onPoint, loadContent = readScanPageContent}: {
  page: ScanPage; number: number; findings?: F[]; selectedFinding?: string;
  onFinding?: (f: F) => void; pickPoint?: boolean; onPoint?: (r: FindingRegion) => void; loadContent?: (id: string, signal?: AbortSignal) => Promise<Blob>;
}) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [retry, setRetry] = useState(0);
  const [regionNotice, setRegionNotice] = useState("");
  const frame = useRef<HTMLDivElement>(null);
  const [fitWidth, setFitWidth] = useState(0);
  const regions = useMemo(() => findings.filter(f => f.scan_page_id === page.id).map(f => ({f, style: regionStyle(f.region)})), [findings, page.id]);
  const regionCounts = useMemo(() => {
    const counts = new Map<string, number>();
    regions.forEach(({style}) => {const key = JSON.stringify(style); if (key) counts.set(key, (counts.get(key) || 0) + 1);});
    return counts;
  }, [regions]);
  useEffect(() => {
    const measure = () => {
      const node = frame.current;
      if (!node || !page.width_px || !page.height_px || !node.clientHeight) return;
      setFitWidth(Math.min(node.clientWidth, node.clientHeight * page.width_px / page.height_px));
    };
    measure();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : undefined;
    if (frame.current) observer?.observe(frame.current);
    window.addEventListener("resize", measure);
    return () => {observer?.disconnect(); window.removeEventListener("resize", measure);};
  }, [page.width_px, page.height_px]);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    setUrl(""); setError(""); setZoom(1); setRegionNotice("");
    if (!page.content_url) {setError("Страница больше не доступна."); return () => controller.abort();}
    loadContent(page.id, controller.signal).then(blob => {
      if (!controller.signal.aborted) {objectUrl = URL.createObjectURL(blob); setUrl(objectUrl);}
    }).catch(e => {
      if (!controller.signal.aborted) setError(e instanceof ApiError && [404, 410].includes(e.status) ? "Страница больше не доступна." : e instanceof ApiError && [401, 403].includes(e.status) ? "Нет доступа к странице." : "Не удалось загрузить страницу. Повторите загрузку.");
    });
    return () => {controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl);};
  }, [page.id, page.content_url, retry, loadContent]);
  return <>
    <div className="matching-viewer-heading"><h2>Страница {number}</h2><div className="button-row">
      <button className="secondary" aria-label="Уменьшить страницу" disabled={!url || zoom <= 1} onClick={() => setZoom(z => Math.max(1, z - .25))}>−</button>
      <button className="secondary" disabled={!url || zoom === 1} onClick={() => setZoom(1)}>По размеру</button>
      <button className="secondary" aria-label="Увеличить страницу" disabled={!url || zoom >= 3} onClick={() => setZoom(z => Math.min(3, z + .25))}>+</button>
    </div></div>
    <div ref={frame} className="matching-image-frame" aria-busy={!url && !error}>
      {error ? <p role="status">{error} <button className="secondary" onClick={() => setRetry(n => n + 1)}>Повторить загрузку</button></p> : url ?
        findings.length || onPoint ? <div className={`review-image-surface ${pickPoint ? "pick-point" : ""}`} style={{width: fitWidth ? `${zoom * fitWidth}px` : `${zoom * 100}%`}} onClick={e => {
          if (!pickPoint || !onPoint) return;
          const rect = e.currentTarget.getBoundingClientRect();
          onPoint({kind: "point", coordinate_space: "normalized_upright_v1", x: Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)), y: Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height))});
        }}>
          <img src={url} alt={`Страница ${number}`} onError={() => {setUrl(""); setError("Не удалось загрузить страницу. Повторите загрузку.");}}/>
          {regions.map(({f, style}) => {
            const sameRegion = (regionCounts.get(JSON.stringify(style)) || 0) > 1;
            return style && <button type="button" key={f.finding_id} className={`review-region ${f.region.kind} ${selectedFinding === f.finding_id ? "selected" : ""}`} style={style} aria-label={`Замечание: ${f.short_explanation}`} aria-pressed={selectedFinding === f.finding_id} onClick={e => {if (!pickPoint) {e.stopPropagation(); if (sameRegion) setRegionNotice("В этом месте несколько замечаний. Выберите нужное в списке заданий."); else {setRegionNotice(""); onFinding?.(f);}}}}/>;
          })}
        </div> : <img src={url} alt={`Страница ${number}`} style={{width: `${zoom * 100}%`, height: zoom === 1 ? "100%" : "auto"}} onError={() => {setUrl(""); setError("Не удалось загрузить страницу. Повторите загрузку.");}}/> : <p role="status">Загрузка страницы…</p>}
    </div>
    {regionNotice && <p role="status">{regionNotice}</p>}
  </>;
}
