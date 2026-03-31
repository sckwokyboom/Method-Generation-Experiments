package com.experiment.retriever.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
public record SearchRequest(
        @JsonProperty("methodSignature") String methodSignature,
        @JsonProperty("classFqn") String classFqn,
        @JsonProperty("supertypes") List<String> supertypes,
        @JsonProperty("imports") List<String> imports,
        @JsonProperty("classFields") List<String> classFields,
        @JsonProperty("siblingSignatures") List<String> siblingSignatures,
        @JsonProperty("siblingOwnerTypes") List<String> siblingOwnerTypes,
        @JsonProperty("siblingUsedTypes") List<String> siblingUsedTypes,
        @JsonProperty("fimPrefix") String fimPrefix,
        @JsonProperty("fimSuffix") String fimSuffix,
        @JsonProperty("targetFilePath") String targetFilePath,
        @JsonProperty("targetBodyStartOffset") int targetBodyStartOffset,
        @JsonProperty("topK") int topK,
        @JsonProperty("nearDuplicateThreshold") double nearDuplicateThreshold
) {}
