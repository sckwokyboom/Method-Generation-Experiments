package com.experiment.shared.model;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

public record SiblingMethod(
        @JsonProperty("signature") String signature,
        @JsonProperty("methodName") String methodName,
        @JsonProperty("parameterTypes") List<String> parameterTypes,
        @JsonProperty("returnType") String returnType,
        @JsonProperty("invocations") List<ResolvedInvocation> invocations,
        @JsonProperty("usedTypes") List<String> usedTypes
) {}
