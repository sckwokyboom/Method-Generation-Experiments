package com.experiment.retriever.search;

import com.experiment.retriever.api.IRetrievalResult;
import com.experiment.retriever.model.SearchRequest;

import java.nio.file.Path;

public final class LeakageFilter {

    private final String targetFilePath;
    private final Path targetPath;
    private final String targetClassFqn;
    private final int targetBodyStartOffset;

    public LeakageFilter(SearchRequest request) {
        this.targetFilePath = request.targetFilePath();
        this.targetPath = Path.of(targetFilePath);
        this.targetClassFqn = request.classFqn();
        this.targetBodyStartOffset = request.targetBodyStartOffset();
    }

    /**
     * Lightweight constructor for external retriever filtering where only
     * the target file path is known (no classFqn / bodyStartOffset).
     */
    public LeakageFilter(String targetFilePath) {
        this.targetFilePath = targetFilePath;
        this.targetPath = Path.of(targetFilePath);
        this.targetClassFqn = "";
        this.targetBodyStartOffset = -1;
    }

    /**
     * Check whether a generic {@link IRetrievalResult} should be excluded.
     * The only real leakage is a result from the same file — in a real
     * FIM scenario that file's content is already in prefix/suffix, not
     * in the retriever's output.
     *
     * Uses {@link Path#endsWith(Path)} for component-level comparison,
     * which correctly handles relative-vs-absolute path mismatches.
     */
    public boolean shouldExclude(IRetrievalResult result) {
        Path location = result.getLocation();
        if (location != null) {
            if (location.equals(targetPath)
                    || location.endsWith(targetPath)
                    || targetPath.endsWith(location)) {
                return true;
            }
        }
        return false;
    }

    public boolean shouldExclude(String filePath, String classFqn, int bodyStartOffset) {
        // Rule 1: exact same method (same class + same offset in the file)
        if (classFqn.equals(targetClassFqn) && bodyStartOffset == targetBodyStartOffset) {
            return true;
        }

        // Rule 2: same file — the file content is already visible via FIM prefix/suffix
        if (filePath.equals(targetFilePath)) {
            return true;
        }

        return false;
    }
}
