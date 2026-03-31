package com.experiment.shared.model;

import com.fasterxml.jackson.annotation.JsonProperty;

public record ExtractionMeta(
        @JsonProperty("timestamp") String timestamp,
        @JsonProperty("durationMs") long durationMs,
        @JsonProperty("totalFiles") int totalFiles,
        @JsonProperty("totalMethods") int totalMethods,
        @JsonProperty("extractedMethods") int extractedMethods,
        @JsonProperty("unresolvedInvocations") int unresolvedInvocations
) {}
