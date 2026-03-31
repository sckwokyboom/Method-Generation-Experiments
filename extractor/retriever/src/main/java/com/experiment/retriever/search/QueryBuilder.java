package com.experiment.retriever.search;

import com.experiment.retriever.index.FieldConstants;
import com.experiment.retriever.model.SearchRequest;
import org.apache.lucene.index.Term;
import org.apache.lucene.search.*;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Builds a Lucene query from a SearchRequest using three focused sub-queries:
 * 1. Type query (typeProfile field) — strongest signal: types used in the method
 * 2. Signature query (methodCard field) — method name, class name, supertypes
 * 3. Invocation query (invocationProfile field) — owner types from sibling invocations
 */
public final class QueryBuilder {
    private static final float TIE_BREAKER = 0.1f;
    private static final Pattern TOKEN_SPLITTER = Pattern.compile("[^a-zA-Z0-9]+");
    private static final Set<String> STOP_WORDS = Set.of(
            // Java keywords
            "public", "private", "protected", "static", "final", "abstract",
            "void", "int", "long", "double", "float", "boolean", "char", "byte", "short",
            "class", "interface", "enum", "extends", "implements", "import", "package",
            "return", "if", "else", "for", "while", "do", "switch", "case", "break",
            "continue", "try", "catch", "finally", "throw", "throws", "new", "this",
            "super", "null", "true", "false",
            // Common Java types (too frequent to be discriminative)
            "string", "object", "list", "map", "set", "optional", "collection",
            "integer", "exception", "override", "deprecated",
            // Common code words
            "get", "is", "has", "the", "and", "not", "java", "lang", "util",
            // Common architecture patterns (low discriminative value)
            "logger", "log", "builder", "config", "service", "manager",
            "factory", "handler", "listener", "event", "plugin",
            "type", "name", "value", "result", "data", "info", "node", "context"
    );

    private QueryBuilder() {}

    public static Query build(SearchRequest request) {
        List<Query> disjuncts = new ArrayList<>();

        // 1. Type query — "find methods working with the same types"
        Query typeQuery = buildTypeQuery(request);
        if (typeQuery != null) {
            disjuncts.add(new BoostQuery(typeQuery, 5.0f));
        }

        // 2. Signature query — "find methods with similar names in similar classes"
        Query sigQuery = buildSignatureQuery(request);
        if (sigQuery != null) {
            disjuncts.add(new BoostQuery(sigQuery, 3.0f));
        }

        // 3. Invocation query — "find methods calling similar owner types"
        Query invQuery = buildInvocationQuery(request);
        if (invQuery != null) {
            disjuncts.add(new BoostQuery(invQuery, 1.0f));
        }

        if (disjuncts.isEmpty()) {
            return new TermQuery(new Term(FieldConstants.METHOD_CARD,
                    extractSimpleName(request.classFqn()).toLowerCase()));
        }

        return new DisjunctionMaxQuery(disjuncts, TIE_BREAKER);
    }

    /**
     * Type query: searches typeProfile field with simple type names from the target method's
     * parameter types, return type, and class field types.
     */
    private static Query buildTypeQuery(SearchRequest request) {
        Set<String> typeTerms = new LinkedHashSet<>();

        // Parameter types — strongest signal
        if (request.imports() != null) {
            for (String imp : request.imports()) {
                typeTerms.add(extractSimpleName(imp).toLowerCase());
            }
        }
        if (request.classFields() != null) {
            for (String field : request.classFields()) {
                typeTerms.add(extractSimpleName(field).toLowerCase());
            }
        }
        if (request.supertypes() != null) {
            for (String st : request.supertypes()) {
                typeTerms.add(extractSimpleName(st).toLowerCase());
            }
        }
        // Types from method signature itself
        addSimpleNames(typeTerms, request.methodSignature());

        // Remove stop words and short terms
        typeTerms.removeIf(t -> t.length() < 3 || STOP_WORDS.contains(t));

        if (typeTerms.isEmpty()) return null;

        BooleanQuery.Builder builder = new BooleanQuery.Builder();
        int count = 0;
        for (String term : typeTerms) {
            if (count >= 80) break;
            builder.add(new TermQuery(new Term(FieldConstants.TYPE_PROFILE, term)),
                    BooleanClause.Occur.SHOULD);
            count++;
        }
        return count > 0 ? builder.build() : null;
    }

    /**
     * Signature query: searches methodCard with just the method name and class name.
     * Compact and specific — no imports or invocations.
     */
    private static Query buildSignatureQuery(SearchRequest request) {
        BooleanQuery.Builder builder = new BooleanQuery.Builder();
        int count = 0;

        // Method name terms (from signature)
        if (request.methodSignature() != null) {
            // Extract just the method name part (before the parenthesis)
            String sig = request.methodSignature();
            int paren = sig.indexOf('(');
            String nameArea = paren > 0 ? sig.substring(0, paren) : sig;
            count += addTerms(builder, FieldConstants.METHOD_CARD, nameArea, BooleanClause.Occur.SHOULD, 10);
        }

        // Class name
        String className = extractSimpleName(request.classFqn());
        if (!className.isEmpty() && className.length() >= 3 && !STOP_WORDS.contains(className.toLowerCase())) {
            builder.add(new TermQuery(new Term(FieldConstants.METHOD_CARD, className.toLowerCase())),
                    BooleanClause.Occur.SHOULD);
            count++;
        }

        // Supertypes
        if (request.supertypes() != null) {
            for (String st : request.supertypes()) {
                String simple = extractSimpleName(st).toLowerCase();
                if (simple.length() >= 3 && !STOP_WORDS.contains(simple)) {
                    builder.add(new TermQuery(new Term(FieldConstants.METHOD_CARD, simple)),
                            BooleanClause.Occur.SHOULD);
                    count++;
                }
            }
        }

        return count > 0 ? builder.build() : null;
    }

    /**
     * Invocation query: searches invocationProfile with owner type simple names
     * from sibling methods. This finds methods that call similar APIs.
     */
    private static Query buildInvocationQuery(SearchRequest request) {
        if (request.siblingOwnerTypes() == null || request.siblingOwnerTypes().isEmpty()) {
            return null;
        }

        BooleanQuery.Builder builder = new BooleanQuery.Builder();
        int count = 0;
        int limit = Math.min(request.siblingOwnerTypes().size(), 20);

        for (int i = 0; i < limit; i++) {
            String type = request.siblingOwnerTypes().get(i).toLowerCase();
            if (type.length() >= 3 && !STOP_WORDS.contains(type)) {
                builder.add(new TermQuery(new Term(FieldConstants.INVOCATION_PROFILE, type)),
                        BooleanClause.Occur.SHOULD);
                count++;
            }
        }

        return count > 0 ? builder.build() : null;
    }

    private static int addTerms(BooleanQuery.Builder builder, String field,
                                String text, BooleanClause.Occur occur, int maxTerms) {
        if (text == null || text.isBlank()) return 0;
        int count = 0;
        for (String token : TOKEN_SPLITTER.split(text)) {
            if (count >= maxTerms) break;
            String lower = token.toLowerCase();
            if (lower.length() >= 3 && !STOP_WORDS.contains(lower)) {
                builder.add(new TermQuery(new Term(field, lower)), occur);
                count++;
            }
        }
        return count;
    }

    private static String extractSimpleName(String fqn) {
        if (fqn == null || fqn.isEmpty()) return "";
        // Handle generics: "List<com.foo.Bar>" -> take the main type
        int generic = fqn.indexOf('<');
        String base = generic > 0 ? fqn.substring(0, generic) : fqn;
        int dot = base.lastIndexOf('.');
        return dot > 0 ? base.substring(dot + 1) : base;
    }

    private static void addSimpleNames(Set<String> target, String text) {
        if (text == null) return;
        for (String token : TOKEN_SPLITTER.split(text)) {
            if (token.length() >= 3 && Character.isUpperCase(token.charAt(0))) {
                target.add(token.toLowerCase());
            }
        }
    }
}
