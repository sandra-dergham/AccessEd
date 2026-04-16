export type UploadResponse = {
  upload_id: string;
  original_filename: string;
  size_bytes: number;
  status: string;
  report?: object;
};

type UploadProgress =
  | { type: "progress"; percent: number }
  | { type: "indeterminate" }
  | { type: "done" };

export function uploadPdf(
  file: File,
  onProgress: (p: UploadProgress) => void
): Promise<UploadResponse> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "https://accessed-c79k.onrender.com/api/upload");

    const noProgressTimer = window.setTimeout(() => {
      onProgress({ type: "indeterminate" });
    }, 250);

    xhr.upload.onprogress = (event) => {
      window.clearTimeout(noProgressTimer);
      if (event.lengthComputable && event.total > 0) {
        const percent = Math.round((event.loaded / event.total) * 100);
        onProgress({ type: "progress", percent });
      } else {
        onProgress({ type: "indeterminate" });
      }
    };

    xhr.upload.onload = () => {
      window.clearTimeout(noProgressTimer);
      onProgress({ type: "indeterminate" });
    };

    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300) {
          onProgress({ type: "done" });
          resolve(data);
        } else {
          reject(data);
        }
      } catch {
        reject({ detail: "Upload failed." });
      }
    };

    xhr.onerror = () => reject({ detail: "Network error." });

    onProgress({ type: "progress", percent: 0 });
    xhr.send(formData);
  });
}

/**
 * Fetches the generated PDF report for a given upload and triggers a browser download.
 * Calls GET /api/uploads/{upload_id}/report
 */
export async function downloadReport(
  uploadId: string,
  originalFilename: string
): Promise<void> {
  const response = await fetch(
    `https://accessed-c79k.onrender.com/api/uploads/${uploadId}/report`
  );

  if (!response.ok) {
    let detail = "Failed to download report.";
    try {
      const err = await response.json();
      detail = err.detail || detail;
    } catch {
      // ignore parse error
    }
    throw new Error(detail);
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `report-${originalFilename}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}