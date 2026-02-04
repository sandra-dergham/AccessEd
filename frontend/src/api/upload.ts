export type UploadResponse = {
  upload_id: string;
  original_filename: string;
  size_bytes: number;
  status: string;
};

type UploadProgress =
  | { type: "progress"; percent: number }     // real %
  | { type: "indeterminate" }                 // no % available
  | { type: "done" };                         // response received

export function uploadPdf(
  file: File,
  onProgress: (p: UploadProgress) => void
): Promise<UploadResponse> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "http://127.0.0.1:8000/api/upload");

    // If no progress events show up quickly, switch to indeterminate “Uploading…”
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

    // Upload bytes finished sending, but we STILL call it uploading (per your request)
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

    // Start state
    onProgress({ type: "progress", percent: 0 });
    xhr.send(formData);
  });
}
