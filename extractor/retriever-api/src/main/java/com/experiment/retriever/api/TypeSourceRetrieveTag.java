package com.experiment.retriever.api;

import java.util.Objects;

/**
 * A named tag representing a scoring dimension in retrieval results.
 * For example, "type_similarity", "invocation_overlap", etc.
 */
public final class TypeSourceRetrieveTag implements Comparable<TypeSourceRetrieveTag> {

    private final String id;
    private final String name;

    public TypeSourceRetrieveTag(String id, String name) {
        this.id = Objects.requireNonNull(id, "id");
        this.name = Objects.requireNonNull(name, "name");
    }

    public String getId() {
        return id;
    }

    public String getName() {
        return name;
    }

    @Override
    public int compareTo(TypeSourceRetrieveTag other) {
        return this.id.compareTo(other.id);
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (!(o instanceof TypeSourceRetrieveTag that)) return false;
        return id.equals(that.id);
    }

    @Override
    public int hashCode() {
        return id.hashCode();
    }

    @Override
    public String toString() {
        return name + " [" + id + "]";
    }
}
