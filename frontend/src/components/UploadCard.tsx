import { useRef, useState } from "react";
import { uploadPdf, downloadReport, downloadCorrected } from "../api/upload";

const MAX_BYTES = 10 * 1024 * 1024;

function bytesToMB(bytes: number) {
  return (bytes / (1024 * 1024)).toFixed(2);
}

export default function UploadCard() {
  const inputRef = useRef<HTMLInputElement | null>(null);

  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<number>(0);
  const [indeterminate, setIndeterminate] = useState(false);

  const [uploadId, setUploadId] = useState<string | null>(null);
  const [uploadedFilename, setUploadedFilename] = useState<string>("");
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const [downloadingReport, setDownloadingReport] = useState(false);
  const [downloadingCorrected, setDownloadingCorrected] = useState(false);

  function validate(file: File): string | null {
    setSuccess(null);
    const isPdf =
      file.type === "application/pdf" ||
      file.name.toLowerCase().endsWith(".pdf");
    if (!isPdf) return "Only PDF files are allowed.";
    if (file.size === 0) return "File is empty.";
    if (file.size > MAX_BYTES)
      return `File too large. Max is 10 MB (yours is ${bytesToMB(file.size)} MB).`;
    return null;
  }

  async function handleFile(file: File) {
    const validationError = validate(file);
    if (validationError) {
      setError(validationError);
      return;
    }

    setError(null);
    setUploadId(null);
    setDownloadError(null);
    setUploading(true);
    setProgress(0);
    setIndeterminate(false);

    try {
      const result = await uploadPdf(file, (p) => {
        if (p.type === "progress") {
          setIndeterminate(false);
          setProgress(p.percent);
        } else if (p.type === "indeterminate") {
          setIndeterminate(true);
        } else if (p.type === "done") {
          setIndeterminate(false);
          setProgress(100);
        }
      });

      setUploadId(result.upload_id);
      setUploadedFilename(result.original_filename);
      setSuccess(`Analysed successfully: ${file.name}`);
    } catch (e: any) {
      setError(e?.detail || "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  async function handleDownloadReport() {
    if (!uploadId) return;
    setDownloadError(null);
    setDownloadingReport(true);
    try {
      await downloadReport(uploadId, uploadedFilename);
    } catch (e: any) {
      setDownloadError(e?.message || "Failed to download report.");
    } finally {
      setDownloadingReport(false);
    }
  }

  async function handleDownloadCorrected() {
    if (!uploadId) return;
    setDownloadError(null);
    setDownloadingCorrected(true);
    try {
      await downloadCorrected(uploadId, uploadedFilename);
    } catch (e: any) {
      setDownloadError(e?.message || "Failed to download corrected PDF.");
    } finally {
      setDownloadingCorrected(false);
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  }

  return (
    <main className="container" aria-label="AccessEd PDF accessibility checker">
      <div className="card">
        <h1 className="title">AccessEd</h1>
        <p className="subtitle">Upload a PDF and check its accessibility</p>

        <div className="info-box" aria-labelledby="info-heading">
          <p className="info-what">
            <strong id="info-heading">What is AccessEd?</strong> AccessEd is a
            PDF accessibility checker and auto-corrector built on WCAG 2.1
            guidelines. It detects violations such as missing alt text,
            insufficient contrast, unlabelled form fields, and more — then
            automatically fixes what it can and flags the rest for manual review.
          </p>
          <p className="info-privacy">
            🔒 <strong>Your privacy is protected.</strong> Uploaded files are
            processed in memory and deleted immediately after analysis. We do
            not store, log, or share your documents.
          </p>
        </div>

        <div
          className={`dropzone ${dragOver ? "dragover" : ""}`}
          role="button"
          tabIndex={0}
          aria-label="Upload PDF file"
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
          }}
        >
          <div className="drop-title">Drag & drop PDF here</div>
          <div className="drop-hint">or click to choose file</div>

          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleFile(file);
              e.currentTarget.value = "";
            }}
          />
        </div>

        <div className="meta">
          Supported:
          <span className="badge">PDF</span>
          <span className="badge">Max 10 MB</span>
        </div>

        {error && (
          <div className="alert error" role="alert">
            {error}
          </div>
        )}

        {success && (
          <div className="alert success" role="status" aria-live="polite">
            {success}
          </div>
        )}

        {uploading && (
          <div
            className="progress-wrap"
            aria-live="polite"
            aria-label="Upload progress"
          >
            <div className="progress-row">
              <span>Uploading…</span>
              <span>{indeterminate ? "…" : `${progress}%`}</span>
            </div>
            <div className="progress-bar">
              <div
                className={`progress-fill ${indeterminate ? "indeterminate" : ""}`}
                style={indeterminate ? undefined : { width: `${progress}%` }}
              />
            </div>
          </div>
        )}

        {uploadId && !uploading && (
          <div className="report-actions">
            <button
              className="btn-report"
              onClick={handleDownloadReport}
              disabled={downloadingReport}
              aria-busy={downloadingReport}
              aria-label="Download accessibility report as PDF"
            >
              {downloadingReport ? "Generating…" : "⬇ Download PDF Report"}
            </button>

            <button
              className="btn-report"
              onClick={handleDownloadCorrected}
              disabled={downloadingCorrected}
              aria-busy={downloadingCorrected}
              aria-label="Download corrected PDF with accessibility fixes applied"
            >
              {downloadingCorrected ? "Downloading…" : "⬇ Download Corrected PDF"}
            </button>

            {downloadError && (
              <div className="alert error" role="alert">
                {downloadError}
              </div>
            )}
          </div>
        )}
      </div>
    </main>
  );
}