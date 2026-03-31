package com.experiment.shared.model;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

public record ExtractionResult(
        @JsonProperty("projectName") String projectName,
        @JsonProperty("meta") ExtractionMeta meta,
        @JsonProperty("classpath") List<String> classpath,
        @JsonProperty("methods") List<ExtractedMethod> methods
) {}
