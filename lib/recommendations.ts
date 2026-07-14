import type { BattleResult, Recommendation, RecommendationPoint } from "./types";

export type NormalizedRecommendation = Recommendation & {
  key: string;
  models: string[];
  reasons: Record<string, string>;
  isConsensus: boolean;
};

function canonicalize(value: string | undefined): string {
  return (value ?? "").normalize("NFKC").trim().replace(/\s+/g, " ").toLowerCase();
}

function canonicalizeIsbn(value: string | undefined): string {
  return canonicalize(value).replace(/-/g, "");
}

export function recommendationKey(recommendation: Pick<Recommendation, "title" | "author" | "isbn">): string {
  return `${canonicalize(recommendation.title)}|${canonicalize(recommendation.author)}`;
}

export function normalizeRecommendations(models: BattleResult["models"]): NormalizedRecommendation[] {
  const byKey = new Map<string, NormalizedRecommendation>();
  Object.entries(models).forEach(([model, result]) => {
    result.recommendations.forEach((recommendation) => {
      if (!canonicalize(recommendation.title)) return;
      const key = recommendationKey(recommendation);
      const matchedKey = byKey.has(key)
        ? key
        : [...byKey.entries()].find(([, existingRecommendation]) =>
          canonicalize(existingRecommendation.title) === canonicalize(recommendation.title) &&
          (!canonicalize(existingRecommendation.author) || !canonicalize(recommendation.author)) &&
          (
            !canonicalizeIsbn(existingRecommendation.isbn) ||
            !canonicalizeIsbn(recommendation.isbn) ||
            canonicalizeIsbn(existingRecommendation.isbn) === canonicalizeIsbn(recommendation.isbn)
          ),
        )?.[0];
      const existingKey = matchedKey ?? key;
      const existing = byKey.get(existingKey);
      const reason = typeof recommendation.why === "string" ? recommendation.why : "";
      if (!existing) {
        byKey.set(key, {
          ...recommendation,
          key,
          models: [model],
          reasons: reason ? { [model]: reason } : {},
          isConsensus: false,
        });
        return;
      }
      existing.models = [...new Set([...existing.models, model])];
      existing.isConsensus = existing.models.length > 1;
      existing.on_tbr ||= recommendation.on_tbr === true;
      existing.hidden_gem ||= recommendation.hidden_gem === true;
      if (!existing.isbn && recommendation.isbn) existing.isbn = recommendation.isbn;
      if ((existing.year === undefined || existing.year === null || existing.year === "") && recommendation.year !== undefined && recommendation.year !== null && recommendation.year !== "") {
        existing.year = recommendation.year;
      }
      if (!existing.author && recommendation.author) existing.author = recommendation.author;
      existing.comfort_zone = existing.comfort_zone === false || recommendation.comfort_zone === false
        ? false
        : existing.comfort_zone === true || recommendation.comfort_zone === true
          ? true
          : undefined;
      if (reason) existing.reasons[model] = reason;
      const updatedKey = recommendationKey(existing);
      if (updatedKey !== existingKey) {
        byKey.delete(existingKey);
        existing.key = updatedKey;
        byKey.set(updatedKey, existing);
      }
    });
  });
  return [...byKey.values()].map((recommendation) => ({
    ...recommendation,
    isConsensus: recommendation.models.length > 1,
  }));
}

export type MapRecommendation = RecommendationPoint & {
  key: string;
  models: string[];
  reasons: Record<string, string>;
  isConsensus: boolean;
};

export function normalizeMapRecommendations(
  points: RecommendationPoint[],
  normalizedRecommendations?: NormalizedRecommendation[],
): MapRecommendation[] {
  const models = Object.fromEntries(
    points.reduce<Map<string, { recommendations: Recommendation[] }>>((map, point) => {
      const name = point.model_name ?? "Unknown model";
      const entry = map.get(name) ?? { recommendations: [] };
      entry.recommendations.push(point);
      map.set(name, entry);
      return map;
    }, new Map()),
  );
  const source = normalizedRecommendations ?? normalizeRecommendations(models);
  return source.flatMap((recommendation) => {
    const point = points.find((item) => recommendationKey(item) === recommendation.key);
    return point ? [{ ...point, ...recommendation }] : [];
  });
}

export function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(Number.isFinite(value) ? value : minimum, minimum), maximum);
}
