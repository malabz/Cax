export type ScalingRow = {
  genomes: number;
  cactusTime: number;
  ramaxTime: number;
  cactusMemory: number;
  ramaxMemory: number;
};

export type CladeRow = {
  level: "family" | "genus";
  levelZh: "科级" | "属级";
  clade: string;
  cactusTime: number;
  ramaxTime: number;
  cactusMemory: number;
  ramaxMemory: number;
};

export type QualityRow = {
  aligner: string;
  entry: string;
  precision: number;
  recall: number;
  f1: number;
};

export const dataSources = {
  scaling: "seq-num-perf -网页专用.xlsx",
  family: "family-performance(1).csv",
  genus: "genus-performance.csv",
  quality: "Primates_Table.csv",
} as const;

export const scalingRows: ScalingRow[] = [
  { genomes: 4, cactusTime: 80.8, ramaxTime: 16.5, cactusMemory: 68.1, ramaxMemory: 80.06 },
  { genomes: 5, cactusTime: 97.02, ramaxTime: 19.09, cactusMemory: 51.1, ramaxMemory: 82.0 },
  { genomes: 6, cactusTime: 166.34, ramaxTime: 22.9, cactusMemory: 55.2, ramaxMemory: 85.5 },
  { genomes: 7, cactusTime: 245.95, ramaxTime: 25.55, cactusMemory: 55.4, ramaxMemory: 105.7 },
];

export const cladeRows: CladeRow[] = [
  { level: "family", levelZh: "科级", clade: "Bovidae", cactusTime: 58.94, ramaxTime: 3.12, cactusMemory: 97.1, ramaxMemory: 74.5 },
  { level: "family", levelZh: "科级", clade: "Canidae", cactusTime: 45.79, ramaxTime: 2.85, cactusMemory: 59.1, ramaxMemory: 63.5 },
  { level: "family", levelZh: "科级", clade: "Cercopithecidae", cactusTime: 31.2, ramaxTime: 1.1, cactusMemory: 111.0, ramaxMemory: 82.2 },
  { level: "family", levelZh: "科级", clade: "Cervidae", cactusTime: 66.38, ramaxTime: 3.91, cactusMemory: 60.7, ramaxMemory: 76.8 },
  { level: "family", levelZh: "科级", clade: "Felidae", cactusTime: 49.25, ramaxTime: 0.96, cactusMemory: 56.6, ramaxMemory: 63.4 },
  { level: "family", levelZh: "科级", clade: "Lemuridae", cactusTime: 31.97, ramaxTime: 3.9, cactusMemory: 130.5, ramaxMemory: 68.0 },
  { level: "genus", levelZh: "属级", clade: "Bos", cactusTime: 88.66, ramaxTime: 5.32, cactusMemory: 58.4, ramaxMemory: 72.9 },
  { level: "genus", levelZh: "属级", clade: "Canis", cactusTime: 48.3, ramaxTime: 5.8, cactusMemory: 42.6, ramaxMemory: 65.6 },
  { level: "genus", levelZh: "属级", clade: "Cervus", cactusTime: 66.38, ramaxTime: 3.91, cactusMemory: 60.7, ramaxMemory: 76.8 },
  { level: "genus", levelZh: "属级", clade: "Eulemur", cactusTime: 25.12, ramaxTime: 3.8, cactusMemory: 90.4, ramaxMemory: 63.8 },
  { level: "genus", levelZh: "属级", clade: "Felis", cactusTime: 31.23, ramaxTime: 1.82, cactusMemory: 48.7, ramaxMemory: 61.1 },
  { level: "genus", levelZh: "属级", clade: "Macaca", cactusTime: 49.27, ramaxTime: 1.61, cactusMemory: 123.9, ramaxMemory: 118.8 },
];

export const qualityRows: QualityRow[] = [
  { aligner: "Progressive Cactus", entry: "", precision: 0.986, recall: 0.991, f1: 0.989 },
  { aligner: "Cactus (Alignathon version)", entry: "cactus", precision: 0.984, recall: 0.983, f1: 0.983 },
  { aligner: "VISTA-LAGAN", entry: "brudno", precision: 0.978, recall: 0.983, f1: 0.98 },
  { aligner: "Mercator/Pecan", entry: "compara", precision: 0.94, recall: 0.996, f1: 0.967 },
  { aligner: "PSAR-Align", entry: "kimMa", precision: 0.98, recall: 0.995, f1: 0.988 },
  { aligner: "AutoMZ", entry: "minmei.automz", precision: 0.98, recall: 0.992, f1: 0.986 },
  { aligner: "TBA", entry: "minmei.tba", precision: 0.981, recall: 0.992, f1: 0.986 },
  { aligner: "Mugsy", entry: "mugsy", precision: 0.978, recall: 0.996, f1: 0.987 },
  { aligner: "progressiveMauve", entry: "pmauve", precision: 0.971, recall: 0.997, f1: 0.984 },
  { aligner: "Robusta", entry: "robusta", precision: 0.941, recall: 0.986, f1: 0.963 },
  { aligner: "GenomeMatch", entry: "softberry.v1", precision: 0.898, recall: 0.99, f1: 0.945 },
  { aligner: "GenomeMatch", entry: "softberry.v2", precision: 0.898, recall: 0.972, f1: 0.933 },
  { aligner: "GenomeMatch", entry: "softberry.v3", precision: 0.905, recall: 0.261, f1: 0.405 },
  { aligner: "MULTIZ", entry: "ucsc", precision: 0.98, recall: 0.992, f1: 0.986 },
  { aligner: "RaMAx", entry: "", precision: 0.99396, recall: 0.97632, f1: 0.98506 },
];

export function sum(values: number[]) {
  return values.reduce((total, value) => total + value, 0);
}

export function speedup(cactusTime: number, ramaxTime: number) {
  return cactusTime / ramaxTime;
}

export function reduction(cactusTime: number, ramaxTime: number) {
  return 100 * (1 - ramaxTime / cactusTime);
}

export function fmt(value: number, digits = 1) {
  return value.toFixed(digits);
}

export const metrics = (() => {
  const totalCactusTime = sum(cladeRows.map((row) => row.cactusTime));
  const totalRamaxTime = sum(cladeRows.map((row) => row.ramaxTime));
  const cladeSpeedups = cladeRows.map((row) => speedup(row.cactusTime, row.ramaxTime));
  const familyRows = cladeRows.filter((row) => row.level === "family");
  const genusRows = cladeRows.filter((row) => row.level === "genus");
  const familySpeedup = sum(familyRows.map((row) => row.cactusTime)) / sum(familyRows.map((row) => row.ramaxTime));
  const genusSpeedup = sum(genusRows.map((row) => row.cactusTime)) / sum(genusRows.map((row) => row.ramaxTime));
  const sortedSpeedups = [...cladeSpeedups].sort((a, b) => a - b);
  const cactusQuality = qualityRows.find((row) => row.aligner === "Progressive Cactus");
  const ramaxQuality = qualityRows.find((row) => row.aligner === "RaMAx");
  const cactusGrowth = scalingRows[scalingRows.length - 1].cactusTime / scalingRows[0].cactusTime;
  const ramaxGrowth = scalingRows[scalingRows.length - 1].ramaxTime / scalingRows[0].ramaxTime;
  const cactusSlope =
    (scalingRows[scalingRows.length - 1].cactusTime - scalingRows[0].cactusTime) /
    (scalingRows[scalingRows.length - 1].genomes - scalingRows[0].genomes);
  const ramaxSlope =
    (scalingRows[scalingRows.length - 1].ramaxTime - scalingRows[0].ramaxTime) /
    (scalingRows[scalingRows.length - 1].genomes - scalingRows[0].genomes);

  if (!cactusQuality || !ramaxQuality) {
    throw new Error("Quality table must include Progressive Cactus and RaMAx.");
  }

  return {
    totalCactusTime,
    totalRamaxTime,
    totalSpeedup: totalCactusTime / totalRamaxTime,
    totalReduction: 100 * (1 - totalRamaxTime / totalCactusTime),
    maxCladeSpeedup: Math.max(...cladeSpeedups),
    minCladeSpeedup: Math.min(...cladeSpeedups),
    medianCladeSpeedup: sortedSpeedups[Math.floor(sortedSpeedups.length / 2)],
    memoryLowerCount: cladeRows.filter((row) => row.ramaxMemory <= row.cactusMemory).length,
    familySpeedup,
    genusSpeedup,
    cactusGrowth,
    ramaxGrowth,
    cactusSlope,
    ramaxSlope,
    scalingSpeedupStart: speedup(scalingRows[0].cactusTime, scalingRows[0].ramaxTime),
    scalingSpeedupEnd: speedup(
      scalingRows[scalingRows.length - 1].cactusTime,
      scalingRows[scalingRows.length - 1].ramaxTime
    ),
    cactusQuality,
    ramaxQuality,
    f1Retained: 100 * (ramaxQuality.f1 / cactusQuality.f1),
    f1Gap: cactusQuality.f1 - ramaxQuality.f1,
    precisionGain: ramaxQuality.precision - cactusQuality.precision,
  };
})();
