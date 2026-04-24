type ProgressProps = {
  steps: string[];
  currentStep: number;
};

export function Progress({ steps, currentStep }: ProgressProps) {
  return (
    <section style={{ marginTop: "20px" }}>
      <h2 style={{ fontSize: "18px", marginBottom: "12px" }}>파이프라인 진행 단계</h2>
      <ol style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: "8px" }}>
        {steps.map((step, index) => {
          const stepIndex = index + 1;
          const isDone = stepIndex < currentStep;
          const isCurrent = stepIndex === currentStep;
          const background = isDone ? "#dcfce7" : isCurrent ? "#dbeafe" : "#f3f4f6";

          return (
            <li key={step} style={{ background, borderRadius: "8px", padding: "10px 12px" }}>
              {stepIndex}. {step}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
