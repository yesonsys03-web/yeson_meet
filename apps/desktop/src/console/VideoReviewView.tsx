type VideoReviewViewProps = {
  jobId: string;
  operatorToken: string;
  onBack: () => void;
};

export function VideoReviewView({ jobId, onBack }: VideoReviewViewProps) {
  return (
    <div>
      <button type="button" onClick={onBack}>← 목록으로</button>
      <p>검수 뷰 (구현 예정): {jobId}</p>
    </div>
  );
}
