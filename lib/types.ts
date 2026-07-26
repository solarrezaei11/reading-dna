export type Book = {
  title: string;
  author?: string;
  isbn?: string;
  year?: string | number | null;
  my_rating?: number;
  [key: string]: unknown;
};

export type TasteDimension =
  | "prose_density"
  | "pacing_preference"
  | "intellectual_depth"
  | "emotional_intensity"
  | "contrarian_score"
  | "fiction_ratio";

export type DnaProfile = {
  reader_archetype?: string;
  taste_summary?: string;
  one_liner?: string;
  taste_dimensions?: Partial<Record<TasteDimension, number | null>>;
  top_themes?: string[];
  avoid_themes?: string[];
  blind_spot_genres?: string[];
  top_books?: Book[];
  avg_rating?: number;
  warnings?: string[];
};

export type Recommendation = {
  title: string;
  author?: string;
  isbn?: string;
  year?: string | number | null;
  why?: string;
  comfort_zone?: boolean;
  on_tbr?: boolean;
  hidden_gem?: boolean;
  [key: string]: unknown;
};

export type ModelMeta = {
  latency_ms?: number | null;
  ttft_ms?: number | null;
  generation_ms?: number | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
};

export type BattleModel = {
  recommendations: Recommendation[];
  meta?: ModelMeta | null;
  info?: {
    display?: string;
    description?: string;
    family?: string;
    family_display?: string;
    provider?: string;
    provider_display?: string;
  };
  error?: string;
};

export type BattleResult = {
  models: Record<string, BattleModel>;
  rubric?: Record<string, unknown>;
  winner?: string | null;
  tie?: boolean;
  warnings?: string[];
};

export type MapPoint = {
  title: string;
  author?: string;
  isbn?: string;
  my_rating: number;
  cluster_id: number;
  cluster_name: string;
  x: number;
  y: number;
};

export type GenreAnchor = { name: string; x: number; y: number; explored?: boolean };

export type RecommendationPoint = Recommendation & {
  model_name?: string;
  x: number;
  y: number;
};

export type MapData = {
  points: MapPoint[];
  genre_anchors: GenreAnchor[];
  rec_points: RecommendationPoint[];
  warnings?: string[];
};

export type LibbyAvailability = {
  status?: "available" | "waitlist" | "not_in_catalog" | "not_found" | "invalid_isbn" | "error" | string;
  available?: boolean;
  wait_weeks?: number | null;
  holds_count?: number;
  owned_copies?: number;
  error?: string;
  title?: string;
  url?: string;
  [key: string]: unknown;
};

export type LibbyResponse = {
  library_found: boolean;
  skipped_reason?: "no_isbns";
  library_name?: string;
  matched_library_name?: string;
  library_key?: string;
  alternatives?: string[];
  results: Record<string, LibbyAvailability>;
  warnings?: string[];
};

export type JudgeVerdict = {
  scores?: Record<string, number>;
  verdict?: string;
  latency_ms?: number;
  model?: string;
  error?: string;
};

export type JudgeResponse = {
  judge: Record<string, JudgeVerdict>;
  winner?: string | null;
  tie?: boolean;
  errors?: Record<string, string>;
  warnings?: string[];
};

export type PredictionDriver = { direction?: "+" | "-"; factor?: string };
export type Prediction = {
  predicted_rating?: number;
  confidence?: number;
  why?: string;
  drivers?: PredictionDriver[];
  meta?: ModelMeta;
  error?: string;
};

export type PredictResponse = {
  already_read: boolean;
  book: Book;
  actual_rating?: number;
  resolved?: boolean;
  predictions?: Record<string, Prediction>;
  neighbors?: Array<{ title: string; my_rating?: number; similarity?: number }>;
  stages?: { resolve_ms?: number; embed_ms?: number; llm_ms?: number; total_ms?: number };
  warnings?: string[];
};

export type AnalysisInput = {
  version: 1;
  source: "csv" | "rss";
  books: Book[];
  currentlyReading: Book[];
  dnf: Book[];
  wantToRead: Book[];
  library: string;
  warnings?: string[];
};
