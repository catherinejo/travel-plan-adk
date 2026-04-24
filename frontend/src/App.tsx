import { useMemo, useState } from "react";
import { requestReport } from "./api/client";
import { DownloadButton } from "./components/DownloadButton";
import { FileUpload } from "./components/FileUpload";
import { Progress } from "./components/Progress";

const STAGES = ["업로드", "파싱", "취합", "리포트 생성"] as const;

export default function App() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [step, setStep] = useState(0);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = useMemo(() => selectedFile !== null && !isLoading, [selectedFile, isLoading]);

  const handleProcess = async () => {
    if (!selectedFile) {
      return;
    }

    try {
      setIsLoading(true);
      setError(null);
      setStep(1);
      const result = await requestReport(selectedFile, (currentStep) => {
        setStep(Math.max(1, Math.min(currentStep, STAGES.length)));
      });
      setDownloadUrl(result.downloadUrl);
      setStep(STAGES.length);
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "요청 처리 중 오류가 발생했습니다.");
      setStep(0);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <main style={{ maxWidth: "720px", margin: "0 auto", padding: "24px" }}>
      <h1>Travel Plan Report</h1>
      <FileUpload
        onFileSelected={(file) => {
          setSelectedFile(file);
          setDownloadUrl(null);
          setStep(0);
          setError(null);
        }}
      />
      <div style={{ marginTop: "16px" }}>
        <button type="button" onClick={handleProcess} disabled={!canSubmit}>
          {isLoading ? "처리 중..." : "리포트 생성"}
        </button>
      </div>
      <Progress steps={[...STAGES]} currentStep={step} />
      {error ? <p style={{ color: "crimson" }}>{error}</p> : null}
      <DownloadButton url={downloadUrl} filename="travel-report.pdf" />
    </main>
  );
}
