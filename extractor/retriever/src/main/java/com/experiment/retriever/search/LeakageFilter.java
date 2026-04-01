package com.experiment.retriever.search;

import com.experiment.retriever.api.IRetrievalResult;
import com.experiment.retriever.model.SearchRequest;

import java.nio.file.Path;

public final class LeakageFilter {

    private final String targetFilePath;
    private final String targetClassFqn;
    private final int targetBodyStartOffset;

    public LeakageFilter(SearchRequest request) {
        this.targetFilePath = request.targetFilePath();
        this.targetClassFqn = request.classFqn();
        this.targetBodyStartOffset = request.targetBodyStartOffset();
    }

    /**
     * Lightweight constructor for external retriever filtering where only
     * the target file path is known (no classFqn / bodyStartOffset).
     */
    public LeakageFilter(String targetFilePath) {
        this.targetFilePath = targetFilePath;
        this.targetClassFqn = "";
        this.targetBodyStartOffset = -1;
    }

    /**
     * Check whether a generic {@link IRetrievalResult} should be excluded.
     * The only real leakage is a result from the same file — in a real
     * FIM scenario that file's content is already in prefix/suffix, not
     * in the retriever's output.
     */
    public boolean shouldExclude(IRetrievalResult result) {
        Path location = result.getLocation();
        if (location != null) {
            String path = location.toString();
            if (path.equals(targetFilePath) || pathEndsWith(path, targetFilePath)
                    || pathEndsWith(targetFilePath, path)) {
                return true;
            }
        }
        return false;
    }

    private static boolean pathEndsWith(String longer, String shorter) {
        return longer.endsWith("/" + shorter) || longer.endsWith("\\" + shorter);
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
