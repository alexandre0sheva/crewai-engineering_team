#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { DurationError } from "./errors.ts";
import { formatDuration } from "./format.ts";
import { parseDuration } from "./parse.ts";
import { summarise } from "./stats.ts";

const USAGE = "usage: durations total DURATION... | durations stats --file FILE";

function readList(path: string): string[] {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    throw new DurationError(`cannot read ${path}`);
  }
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "" && !line.startsWith("#"));
}

export function main(args: readonly string[]): string[] {
  const [command, ...rest] = args;
  if (command === "total") {
    if (rest.length === 0) {
      throw new DurationError(USAGE);
    }
    const total = rest.map(parseDuration).reduce((sum, value) => sum + value, 0);
    return [formatDuration(total)];
  }
  if (command === "stats") {
    if (rest.length !== 2 || rest[0] !== "--file") {
      throw new DurationError(USAGE);
    }
    const lines = readList(rest[1]);
    if (lines.length === 0) {
      throw new DurationError(`no durations in ${rest[1]}`);
    }
    const summary = summarise(lines.map(parseDuration));
    return [
      `count: ${summary.count}`,
      `total: ${formatDuration(summary.total)}`,
      `fastest: ${formatDuration(summary.fastest)}`,
      `slowest: ${formatDuration(summary.slowest)}`,
      `average: ${formatDuration(summary.average)}`,
    ];
  }
  throw new DurationError(USAGE);
}

if (import.meta.main) {
  try {
    for (const line of main(process.argv.slice(2))) {
      console.log(line);
    }
  } catch (error) {
    if (error instanceof DurationError) {
      console.error(`error: ${error.message}`);
      process.exit(2);
    }
    throw error;
  }
}
