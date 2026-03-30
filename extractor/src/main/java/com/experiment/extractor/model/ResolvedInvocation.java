package com.experiment.extractor.model;

import com.fasterxml.jackson.annotation.JsonProperty;

public record ResolvedInvocation(
        @JsonProperty("signature") String signature,
        @JsonProperty("resolutionMode") String resolutionMode,
        @JsonProperty("orderIndex") int orderIndex
) {}
