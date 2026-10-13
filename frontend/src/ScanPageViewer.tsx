import {useEffect, useState} from "react";
import {ApiError} from "./api";
import {readScanPageContent, type ScanPage} from "./paperScanApi";

export function ScanPageViewer({page, number}: {page: ScanPage; number: number}) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    setUrl(""); setError(""); setZoom(1);
    if (!page.content_url) {setError("Страница больше не доступна."); return () => controller.abort();}
    readScanPageContent(page.id, controller.signal).then(blob => {
      if (!controller.signal.aborted) {objectUrl = URL.createObjectURL(blob); setUrl(objectUrl);}
    }).catch(e => {
      if (!controller.signal.aborted) setError(e instanceof ApiError && [404, 410].includes(e.status) ? "Страница больше не доступна." : e instanceof ApiError && [401, 403].includes(e.status) ? "Нет доступа к странице." : "Не удалось загрузить страницу. Повторите загрузку.");
    });
    return () => {controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl);};
  }, [page.id, page.content_url, retry]);
  return <>
    <div className="matching-viewer-heading"><h2>Страница {number}</h2><div className="button-row">
      <button className="secondary" aria-label="Уменьшить страницу" disabled={!url || zoom <= 1} onClick={() => setZoom(z => Math.max(1, z - .25))}>−</button>
      <button className="secondary" disabled={!url || zoom === 1} onClick={() => setZoom(1)}>По размеру</button>
      <button className="secondary" aria-label="Увеличить страницу" disabled={!url || zoom >= 3} onClick={() => setZoom(z => Math.min(3, z + .25))}>+</button>
    </div></div>
    <div className="matching-image-frame" aria-busy={!url && !error}>
      {error ? <p role="status">{error} <button className="secondary" onClick={() => setRetry(n => n + 1)}>Повторить загрузку</button></p> : url ?
        <img src={url} alt={`Страница ${number}`} style={{width: `${zoom * 100}%`, height: zoom === 1 ? "100%" : "auto"}} onError={() => {setUrl(""); setError("Не удалось загрузить страницу. Повторите загрузку.");}}/> : <p role="status">Загрузка страницы…</p>}
    </div>
  </>;
}
