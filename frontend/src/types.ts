export interface User {
  id: string;
  username: string;
  position: string;
}
export interface Draft {
  content: string;
  count: number;
}
export interface Template {
  template_id: string;
  name?: string;
  source_file: string;
  slide_count: number;
  pattern_count: number;
}
export interface Issue {
  id: string;
  slide?: number;
  severity: string;
  message: string;
  repairable?: boolean;
  bounds?: { x: number; y: number; w: number; h: number }[];
}
export interface Deck {
  presentation_id: string;
  slide_count: number;
  planner_mode?: string;
  variant?: { label: string; id: string };
  qa: {
    status: string;
    issues: Issue[];
    canvas?: { width_inches: number; height_inches: number };
  };
  exports?: {
    artifacts?: Record<string, string>;
    previews?: { slide: number }[];
    issues?: { code: string; message: string }[];
  };
}
export interface Batch {
  batch_id: string;
  status: string;
  variants: Deck[];
  diversity_status?: string;
}
export interface HistoryItem {
  batch_id: string;
  status: string;
  template_id: string;
}
export interface Job {
  job_id: string;
  status: string;
  stage: string;
  progress: number;
  error?: string;
  batch?: Batch;
}
