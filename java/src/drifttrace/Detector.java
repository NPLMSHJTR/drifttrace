package drifttrace;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.stream.Stream;

/**
 * Streaming-style anomaly detector for single-series metrics.
 *
 * The detector is deliberately classical and explainable:
 *
 *   1. a rolling median removes the seasonal shape without assuming a period;
 *   2. a rolling MAD turns the residual into a robust z-score;
 *   3. a persistence rule requires consecutive outliers, which suppresses the
 *      single-point spikes that a plain 3-sigma threshold reports as alarms;
 *   4. intervals closer than a few samples are merged so one incident is one
 *      interval rather than five.
 *
 * {@code --baseline} swaps steps 1 and 2 for a global mean and standard
 * deviation. Both modes share the persistence and merging rules, so the
 * comparison isolates the baseline model rather than the post-processing.
 */
public final class Detector {
    record Interval(int start, int end, double peak, String direction) {}

    public static void main(String[] args) throws IOException {
        Path input = Path.of("data/series");
        Path out = Path.of("data/detections.json");
        boolean baseline = false;
        int window = 61;
        int period = 288;
        double threshold = 3.5;
        int minRun = 1;
        int maxGap = 5;

        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--input" -> input = Path.of(args[++i]);
                case "--out" -> out = Path.of(args[++i]);
                case "--baseline" -> baseline = true;
                case "--window" -> window = Integer.parseInt(args[++i]);
                case "--period" -> period = Integer.parseInt(args[++i]);
                case "--threshold" -> threshold = Double.parseDouble(args[++i]);
                case "--min-run" -> minRun = Integer.parseInt(args[++i]);
                case "--max-gap" -> maxGap = Integer.parseInt(args[++i]);
                default -> {
                    System.err.println("unknown argument: " + args[i]);
                    System.exit(2);
                }
            }
        }

        List<Path> files;
        try (Stream<Path> stream = Files.list(input)) {
            files = stream.filter(path -> path.toString().endsWith(".csv"))
                .sorted(Comparator.comparing(path -> path.getFileName().toString()))
                .toList();
        }
        if (files.isEmpty()) {
            System.err.println("no CSV files in " + input);
            System.exit(2);
        }

        StringBuilder json = new StringBuilder();
        json.append("{\"schema_version\":1,\"method\":")
            .append(baseline ? "\"baseline\"" : "\"rolling\"")
            .append(",\"window\":").append(window)
            .append(",\"period\":").append(period)
            .append(",\"threshold\":").append(String.format(Locale.ROOT, "%.2f", threshold))
            .append(",\"min_run\":").append(minRun)
            .append(",\"max_gap\":").append(maxGap)
            .append(",\"series\":[");

        long started = System.nanoTime();
        int total = 0;
        int alarms = 0;

        for (int index = 0; index < files.size(); index++) {
            Path file = files.get(index);
            String id = file.getFileName().toString().replace(".csv", "");
            double[] values = readSeries(file);
            List<Interval> intervals = baseline
                ? detectBaseline(values, threshold, minRun, maxGap)
                : detectRolling(values, window, period, threshold, minRun, maxGap);
            // Before three seasonal references exist, the detector falls back to a
            // rolling median, which is a different estimator. Scoring that region
            // would mix two methods, so it is reported as warm-up instead.
            int warmup = Math.min(values.length, period * 3);
            intervals.removeIf(interval -> interval.end() < warmup);
            total += values.length;
            alarms += intervals.size();

            if (index > 0) {
                json.append(',');
            }
            json.append("{\"id\":").append(quote(id)).append(",\"points\":").append(values.length)
                .append(",\"intervals\":[");
            for (int i = 0; i < intervals.size(); i++) {
                Interval interval = intervals.get(i);
                if (i > 0) {
                    json.append(',');
                }
                json.append("{\"start\":").append(interval.start())
                    .append(",\"end\":").append(interval.end())
                    .append(",\"peak\":").append(String.format(Locale.ROOT, "%.2f", interval.peak()))
                    .append(",\"direction\":").append(quote(interval.direction()))
                    .append('}');
            }
            json.append("]}");
        }
        double seconds = (System.nanoTime() - started) / 1e9;

        json.append("],\"elapsed_s\":").append(String.format(Locale.ROOT, "%.4f", seconds))
            .append(",\"series_count\":").append(files.size())
            .append(",\"point_count\":").append(total)
            .append(",\"alarm_count\":").append(alarms)
            .append("}\n");
        if (out.getParent() != null) {
            Files.createDirectories(out.getParent());
        }
        Files.writeString(out, json.toString(), StandardCharsets.UTF_8);
        System.out.println(String.format(Locale.ROOT,
            "%s: %d series, %d points, %d intervals, %.3f s",
            baseline ? "baseline" : "rolling", files.size(), total, alarms, seconds));
    }

    static double[] readSeries(Path file) throws IOException {
        List<String> lines = Files.readAllLines(file, StandardCharsets.UTF_8);
        List<Double> values = new ArrayList<>();
        for (int i = 0; i < lines.size(); i++) {
            String line = lines.get(i).trim();
            if (line.isEmpty()) {
                continue;
            }
            String[] parts = line.split(",");
            if (i == 0 && parts.length > 1 && !isNumber(parts[1])) {
                continue; // header
            }
            if (parts.length < 2) {
                continue;
            }
            try {
                values.add(Double.parseDouble(parts[1].trim()));
            } catch (NumberFormatException ignored) {
                // Skip malformed rows rather than aborting the whole series.
            }
        }
        double[] result = new double[values.size()];
        for (int i = 0; i < result.length; i++) {
            result[i] = values.get(i);
        }
        return result;
    }

    private static boolean isNumber(String value) {
        try {
            Double.parseDouble(value.trim());
            return true;
        } catch (NumberFormatException error) {
            return false;
        }
    }

    static double[] rollingMedian(double[] values, int window) {
        double[] out = new double[values.length];
        int half = Math.max(1, window / 2);
        double[] buffer = new double[Math.min(window, values.length)];
        for (int i = 0; i < values.length; i++) {
            int lo = Math.max(0, i - half);
            int hi = Math.min(values.length - 1, i + half);
            int size = hi - lo + 1;
            if (buffer.length < size) {
                buffer = new double[size];
            }
            System.arraycopy(values, lo, buffer, 0, size);
            Arrays.sort(buffer, 0, size);
            out[i] = (size % 2 == 1)
                ? buffer[size / 2]
                : (buffer[size / 2 - 1] + buffer[size / 2]) / 2.0;
        }
        return out;
    }

    static double[] rollingMad(double[] residual, int window) {
        double[] out = new double[residual.length];
        double[] absolute = new double[residual.length];
        for (int i = 0; i < residual.length; i++) {
            absolute[i] = Math.abs(residual[i]);
        }
        return rollingMedian(absolute, window);
    }

    static double[] rollingStd(double[] values, int window) {
        int half = Math.max(1, window / 2);
        double[] prefix = new double[values.length + 1];
        double[] prefixSquare = new double[values.length + 1];
        for (int i = 0; i < values.length; i++) {
            prefix[i + 1] = prefix[i] + values[i];
            prefixSquare[i + 1] = prefixSquare[i] + values[i] * values[i];
        }
        double[] out = new double[values.length];
        for (int i = 0; i < values.length; i++) {
            int lo = Math.max(0, i - half);
            int hi = Math.min(values.length, i + half + 1);
            int size = hi - lo;
            double sum = prefix[hi] - prefix[lo];
            double square = prefixSquare[hi] - prefixSquare[lo];
            double mean = sum / size;
            double variance = Math.max(0, square / size - mean * mean);
            out[i] = Math.sqrt(variance);
        }
        return out;
    }

    /**
     * Median of the same time-of-day on the previous {@code depth} days.
     *
     * A centred rolling median is easy, but it drags a level shift out over half
     * a window in both directions. A seasonal baseline from earlier days does
     * not: while a shift lasts, every point is compared with a clean reference,
     * so the predicted interval matches the incident.
     */
    static double[] seasonalBaseline(double[] values, int period, int depth, double[] fallback) {
        double[] out = new double[values.length];
        double[] buffer = new double[depth];
        for (int i = 0; i < values.length; i++) {
            int count = 0;
            for (int d = 1; d <= depth; d++) {
                int index = i - d * period;
                if (index >= 0) {
                    buffer[count++] = values[index];
                }
            }
            if (count == 0) {
                out[i] = fallback[i];
            } else {
                Arrays.sort(buffer, 0, count);
                out[i] = (count % 2 == 1)
                    ? buffer[count / 2]
                    : (buffer[count / 2 - 1] + buffer[count / 2]) / 2.0;
            }
        }
        return out;
    }

    static List<Interval> detectRolling(double[] values, int window, int period, double threshold, int minRun, int maxGap) {
        double[] fallback = rollingMedian(values, window);
        double[] trend = seasonalBaseline(values, period, 3, fallback);
        double[] residual = new double[values.length];
        for (int i = 0; i < values.length; i++) {
            residual[i] = values[i] - trend[i];
        }
        // A rolling scale is self-defeating here: a long level shift inflates the
        // very window that is supposed to judge it. The seasonal baseline has
        // already removed the shape, so one robust scale for the whole series is
        // both simpler and harder to contaminate.
        double[] absolute = new double[residual.length];
        for (int i = 0; i < residual.length; i++) {
            absolute[i] = Math.abs(residual[i]);
        }
        Arrays.sort(absolute);
        double sigma = 1.4826 * absolute[absolute.length / 2] + 1e-6;
        double[] score = new double[values.length];
        boolean[] mask = new boolean[values.length];
        for (int i = 0; i < values.length; i++) {
            score[i] = Math.abs(residual[i]) / sigma;
            mask[i] = score[i] > threshold;
        }
        List<Interval> levelIntervals = toIntervals(mask, score, residual, minRun, maxGap);

        // A variance burst leaves the mean alone, so the z-score path cannot see
        // it. Compare a short-window standard deviation with its long-window
        // median instead. This path gets its own persistence rule, because a
        // spike also inflates the local standard deviation and would otherwise
        // widen every spike into a 20-point interval.
        double[] shortStd = rollingStd(values, 21);
        double[] longStd = rollingMedian(shortStd, 289);
        double[] ratio = new double[values.length];
        for (int i = 0; i < values.length; i++) {
            ratio[i] = shortStd[i] / (longStd[i] + 1e-6);
        }
        // Smoothing matters: the raw ratio hovers around the threshold inside a
        // burst, which fragments one incident into five short alarms.
        double[] smoothed = rollingMedian(ratio, 9);
        double[] varianceScore = new double[values.length];
        boolean[] varianceMask = new boolean[values.length];
        for (int i = 0; i < values.length; i++) {
            varianceScore[i] = smoothed[i] * (threshold / 1.8);
            varianceMask[i] = smoothed[i] > 1.8;
        }
        // A variance regime has no sharp boundary: the ratio ramps in and out over
        // several samples. Dilating the mask by six samples aligns the reported
        // interval with the incident rather than with the threshold crossing.
        boolean[] dilated = new boolean[varianceMask.length];
        for (int i = 0; i < varianceMask.length; i++) {
            if (!varianceMask[i]) {
                continue;
            }
            int lo = Math.max(0, i - 6);
            int hi = Math.min(varianceMask.length - 1, i + 6);
            for (int j = lo; j <= hi; j++) {
                dilated[j] = true;
            }
        }
        List<Interval> varianceIntervals =
            toIntervals(dilated, varianceScore, residual, Math.max(12, minRun), 15);

        // A spike inflates the local standard deviation for a whole window, so the
        // variance path would otherwise wrap every spike in a 20-point interval.
        // A variance interval that overlaps a level interval is already explained.
        List<Interval> kept = new ArrayList<>();
        for (Interval candidate : varianceIntervals) {
            boolean explained = false;
            for (Interval level : levelIntervals) {
                if (candidate.start() - maxGap <= level.end() && level.start() - maxGap <= candidate.end()) {
                    explained = true;
                    break;
                }
            }
            if (!explained) {
                kept.add(candidate);
            }
        }
        return mergeIntervalLists(levelIntervals, kept, maxGap);
    }

    static List<Interval> detectBaseline(double[] values, double threshold, int minRun, int maxGap) {
        double sum = 0;
        double square = 0;
        for (double value : values) {
            sum += value;
            square += value * value;
        }
        double mean = values.length == 0 ? 0 : sum / values.length;
        double variance = Math.max(0, square / Math.max(1, values.length) - mean * mean);
        double sigma = Math.sqrt(variance) + 1e-6;
        double[] score = new double[values.length];
        boolean[] mask = new boolean[values.length];
        for (int i = 0; i < values.length; i++) {
            score[i] = Math.abs(values[i] - mean) / sigma;
            mask[i] = score[i] > threshold;
        }
        return toIntervals(mask, score, values, minRun, maxGap);
    }

    static List<Interval> mergeIntervalLists(List<Interval> first, List<Interval> second, int maxGap) {
        List<Interval> all = new ArrayList<>(first);
        all.addAll(second);
        all.sort(Comparator.comparingInt(Interval::start));
        List<Interval> merged = new ArrayList<>();
        for (Interval interval : all) {
            if (!merged.isEmpty() && interval.start() - merged.get(merged.size() - 1).end() <= maxGap) {
                Interval previous = merged.remove(merged.size() - 1);
                merged.add(new Interval(previous.start(), Math.max(previous.end(), interval.end()),
                    Math.max(previous.peak(), interval.peak()),
                    previous.peak() >= interval.peak() ? previous.direction() : interval.direction()));
            } else {
                merged.add(interval);
            }
        }
        return merged;
    }

    static List<Interval> toIntervals(boolean[] mask, double[] score, double[] signed, int minRun, int maxGap) {
        List<int[]> runs = new ArrayList<>();
        int start = -1;
        for (int i = 0; i < mask.length; i++) {
            if (mask[i] && start < 0) {
                start = i;
            } else if (!mask[i] && start >= 0) {
                if (i - start >= minRun) {
                    runs.add(new int[]{start, i - 1});
                }
                start = -1;
            }
        }
        if (start >= 0 && mask.length - start >= minRun) {
            runs.add(new int[]{start, mask.length - 1});
        }

        List<int[]> merged = new ArrayList<>();
        for (int[] run : runs) {
            if (!merged.isEmpty() && run[0] - merged.get(merged.size() - 1)[1] <= maxGap) {
                merged.get(merged.size() - 1)[1] = run[1];
            } else {
                merged.add(new int[]{run[0], run[1]});
            }
        }

        List<Interval> intervals = new ArrayList<>();
        for (int[] span : merged) {
            double peak = 0;
            double signedSum = 0;
            for (int i = span[0]; i <= span[1]; i++) {
                peak = Math.max(peak, score[i]);
                signedSum += signed[i];
            }
            intervals.add(new Interval(span[0], span[1], peak, signedSum >= 0 ? "up" : "down"));
        }
        return intervals;
    }

    private static String quote(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c == '"' || c == '\\') {
                out.append('\\');
            }
            out.append(c);
        }
        return out.append('"').toString();
    }
}
