package com.experiment.retriever.search;

import com.experiment.retriever.model.SearchRequest;

import java.util.HashSet;
import java.util.Set;
import java.util.regex.Pattern;

public final class LeakageFilter {
    private static final Pattern TOKEN_SPLITTER = Pattern.compile("[^a-zA-Z0-9]+");

    private final String targetFilePath;
    private final String targetClassFqn;
    private final int targetBodyStartOffset;
    private final double nearDuplicateThreshold;

    public LeakageFilter(SearchRequest request) {
        this.targetFilePath = request.targetFilePath();
        this.targetClassFqn = request.classFqn();
        this.targetBodyStartOffset = request.targetBodyStartOffset();
        this.nearDuplicateThreshold = request.nearDuplicateThreshold() > 0
                ? request.nearDuplicateThreshold() : 0.8;
    }

    public boolean shouldExclude(String filePath, String classFqn, int bodyStartOffset,
                                 String methodBody, String targetMethodBody) {
        // Rule 1: exact same method
        if (classFqn.equals(targetClassFqn) && bodyStartOffset == targetBodyStartOffset) {
            return true;
        }

        // Rule 2: same file
        if (filePath.equals(targetFilePath)) {
            return true;
        }

        // Rule 3: near-duplicate by token Jaccard
        if (targetMethodBody != null && methodBody != null
                && !targetMethodBody.isBlank() && !methodBody.isBlank()) {
            double jaccard = tokenJaccard(methodBody, targetMethodBody);
            if (jaccard >= nearDuplicateThreshold) {
                return true;
            }
        }

        return false;
    }

    private double tokenJaccard(String a, String b) {
        Set<String> tokensA = tokenize(a);
        Set<String> tokensB = tokenize(b);
        if (tokensA.isEmpty() && tokensB.isEmpty()) return 1.0;
        if (tokensA.isEmpty() || tokensB.isEmpty()) return 0.0;

        Set<String> intersection = new HashSet<>(tokensA);
        intersection.retainAll(tokensB);

        Set<String> union = new HashSet<>(tokensA);
        union.addAll(tokensB);

        return (double) intersection.size() / union.size();
    }

    private Set<String> tokenize(String text) {
        Set<String> tokens = new HashSet<>();
        for (String token : TOKEN_SPLITTER.split(text)) {
            if (!token.isBlank()) {
                tokens.add(token.toLowerCase());
            }
        }
        return tokens;
    }
}
