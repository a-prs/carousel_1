import { useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import * as api from "../lib/api";

type Slide = { index: number; blobUrl: string; blob: Blob };
type Phase = "idle" | "working" | "done" | "error";

export default function Studio() {
  const { user, logout } = useAuth();
  const [coverFile, setCoverFile] = useState<File | null>(null);
  const [coverPreview, setCoverPreview] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [statusLabel, setStatusLabel] = useState("");
  const [slides, setSlides] = useState<Slide[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [zipBusy, setZipBusy] = useState(false);
  const pollRef = useRef<number | null>(null);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  function onPickCover(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] || null;
    setCoverFile(f);
    setCoverPreview(f ? URL.createObjectURL(f) : null);
  }

  async function onGenerate() {
    setErrorMsg(null);
    setSlides([]);
    setPhase("working");
    try {
      let uploadId: string | undefined;
      if (coverFile) {
        setStatusLabel("Загружаем обложку…");
        uploadId = (await api.uploadCover(coverFile)).upload_id;
      }
      setStatusLabel("Отправляем на генерацию…");
      const { job_id } = await api.generate(text.trim(), uploadId);
      setJobId(job_id);
      await poll(job_id);
    } catch (e: any) {
      setPhase("error");
      setErrorMsg(e?.message === "unauthorized" ? "Сессия истекла, войдите заново" : (e?.message || "Ошибка"));
      if (e?.status === 401) logout();
    }
  }

  function poll(jobId: string): Promise<void> {
    return new Promise((resolve) => {
      setStatusLabel("Генерируем карусель…");
      pollRef.current = window.setInterval(async () => {
        try {
          const s = await api.jobStatus(jobId);
          if (s.status === "done") {
            stopPoll();
            await loadSlides(s.slides);
            setPhase("done");
            setStatusLabel("");
            resolve();
          } else if (s.status === "error") {
            stopPoll();
            setPhase("error");
            setErrorMsg(s.error || "Ошибка генерации");
            resolve();
          } else {
            setStatusLabel(s.status === "running" ? "Рендерим слайды…" : "В очереди…");
          }
        } catch (e: any) {
          stopPoll();
          setPhase("error");
          setErrorMsg(e?.message || "Ошибка");
          resolve();
        }
      }, 1500);
    });
  }

  function stopPoll() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }

  async function loadSlides(items: { index: number; url: string }[]) {
    const out: Slide[] = [];
    for (const it of items) {
      const blob = await api.slideBlob(it.url);
      out.push({ index: it.index, blob, blobUrl: URL.createObjectURL(blob) });
    }
    setSlides(out);
  }

  async function saveSlide(slide: Slide) {
    const name = `slide_${String(slide.index).padStart(2, "0")}.png`;
    const file = new File([slide.blob], name, { type: "image/png" });
    // Primary path (mobile): Web Share -> "Save Image" lands in Photos on iOS.
    const nav = navigator as any;
    if (nav.canShare && nav.canShare({ files: [file] })) {
      try {
        await nav.share({ files: [file] });
        return;
      } catch (err: any) {
        if (err?.name === "AbortError") return; // user cancelled
        // otherwise fall through to download
      }
    }
    // Fallback (desktop / no file share): <a download>.
    const a = document.createElement("a");
    a.href = slide.blobUrl;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async function downloadZip() {
    if (!jobId || zipBusy) return;
    setZipBusy(true);
    try {
      const blob = await api.zipBlob(jobId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `carousel_${jobId}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setErrorMsg(e?.message || "Не удалось скачать zip");
      if (e?.status === 401) logout();
    } finally {
      setZipBusy(false);
    }
  }

  const working = phase === "working";

  return (
    <div className="screen">
      <header className="topbar">
        <span className="brand small">Carousel Studio</span>
        <span className="handle">{user?.ig_handle || user?.slug}</span>
        <button className="link" onClick={logout}>Выйти</button>
      </header>

      <main className="studio">
        <label className="uploader">
          {coverPreview ? (
            <img src={coverPreview} className="cover-preview" alt="обложка" />
          ) : (
            <span className="muted">📷 Фото для обложки (необязательно)</span>
          )}
          <input type="file" accept="image/*" onChange={onPickCover} hidden />
        </label>

        <textarea
          className="text-input"
          placeholder="Вставь текст поста — разобьём на слайды карусели…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={8}
        />

        <button className="primary big" disabled={working || !text.trim()} onClick={onGenerate}>
          {working ? statusLabel || "Работаем…" : "Сгенерировать карусель"}
        </button>

        {phase === "error" && <div className="error">{errorMsg}</div>}

        {phase === "done" && (
          <section className="result">
            <div className="result-head">
              <h2>Готово · {slides.length} слайдов</h2>
              <button className="link" onClick={downloadZip} disabled={zipBusy}>
                {zipBusy ? "Собираем zip…" : "Скачать всё (zip)"}
              </button>
            </div>
            {slides.map((s) => (
              <div className="slide-row" key={s.index}>
                <img src={s.blobUrl} className="slide-img" alt={`слайд ${s.index}`} />
                <button className="primary" onClick={() => saveSlide(s)}>Сохранить</button>
              </div>
            ))}
          </section>
        )}
      </main>
    </div>
  );
}
