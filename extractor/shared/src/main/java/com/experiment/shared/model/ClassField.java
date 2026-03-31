package com.experiment.shared.model;

import com.fasterxml.jackson.annotation.JsonProperty;

public record ClassField(
        @JsonProperty("name") String name,
        @JsonProperty("typeFqn") String typeFqn
) {}
