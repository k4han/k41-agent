import type { ModelCatalog } from "@/types";

export function resolveModelAndProvider(
  provider: string,
  model: string,
  defaultProvider: string,
  defaultModel: string,
  catalogs: ModelCatalog[],
): { provider: string; model: string } {
  const providerName = provider.trim();
  const modelName = model.trim();
  const usesDefaultProvider = !providerName || providerName.toLowerCase() === "default";
  const resolvedProvider = usesDefaultProvider ? defaultProvider : providerName;
  const catalog = catalogs.find((item) => item.provider === resolvedProvider);
  const usesDefaultModel = ["", "default", "provider default"].includes(modelName.toLowerCase());
  const resolvedModel = usesDefaultModel
    ? ((usesDefaultProvider ? defaultModel : "") || catalog?.default_model || "")
    : modelName;
  return { provider: resolvedProvider, model: resolvedModel };
}
