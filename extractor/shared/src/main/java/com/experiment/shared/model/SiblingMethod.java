package com.experiment.shared.model;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

public record SiblingMethod(
        @JsonProperty("signature") String signature,
        @JsonProperty("invocations") List<ResolvedInvocation> invocations,
        @JsonProperty("usedTypes") List<String> usedTypes
) {}
