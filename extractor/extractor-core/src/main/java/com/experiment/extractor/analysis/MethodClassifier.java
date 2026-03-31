package com.experiment.extractor.analysis;

import com.experiment.shared.model.MethodCategory;
import org.eclipse.jdt.core.dom.Annotation;
import org.eclipse.jdt.core.dom.Assignment;
import org.eclipse.jdt.core.dom.Block;
import org.eclipse.jdt.core.dom.ExpressionStatement;
import org.eclipse.jdt.core.dom.IExtendedModifier;
import org.eclipse.jdt.core.dom.MethodDeclaration;
import org.eclipse.jdt.core.dom.MethodInvocation;
import org.eclipse.jdt.core.dom.ReturnStatement;
import org.eclipse.jdt.core.dom.Statement;

import java.util.List;

public class MethodClassifier {

    public static MethodCategory classify(MethodDeclaration method, boolean isInTestSource) {
        if (isInTestSource || hasAnnotation(method, "Test")) {
            return MethodCategory.TEST;
        }
        if (hasAnnotation(method, "Generated")) {
            return MethodCategory.GENERATED;
        }

        Block body = method.getBody();
        if (body == null) return MethodCategory.NORMAL;

        @SuppressWarnings("unchecked")
        List<Statement> statements = body.statements();
        String name = method.getName().getIdentifier();

        if (statements.size() <= 1) {
            if (isGetter(name, method, statements)) return MethodCategory.GETTER;
            if (isSetter(name, method, statements)) return MethodCategory.SETTER;
            if (isDelegator(method, statements)) return MethodCategory.DELEGATOR;
        }

        return MethodCategory.NORMAL;
    }

    private static boolean hasAnnotation(MethodDeclaration method, String annotationName) {
        @SuppressWarnings("unchecked")
        List<IExtendedModifier> modifiers = method.modifiers();
        for (IExtendedModifier mod : modifiers) {
            if (mod.isAnnotation()) {
                Annotation ann = (Annotation) mod;
                String typeName = ann.getTypeName().getFullyQualifiedName();
                if (typeName.equals(annotationName) || typeName.endsWith("." + annotationName)) {
                    return true;
                }
            }
        }
        return false;
    }

    private static boolean isGetter(String name, MethodDeclaration method, List<Statement> statements) {
        if (method.parameters().size() > 0) return false;
        if (!(name.startsWith("get") || name.startsWith("is"))) return false;
        if (statements.size() == 1 && statements.get(0) instanceof ReturnStatement) return true;
        return statements.isEmpty();
    }

    private static boolean isSetter(String name, MethodDeclaration method, List<Statement> statements) {
        if (method.parameters().size() != 1) return false;
        if (!name.startsWith("set")) return false;
        if (statements.size() == 1 && statements.get(0) instanceof ExpressionStatement es) {
            return es.getExpression() instanceof Assignment;
        }
        return false;
    }

    private static boolean isDelegator(MethodDeclaration method, List<Statement> statements) {
        if (statements.size() != 1) return false;
        Statement stmt = statements.get(0);
        if (stmt instanceof ReturnStatement rs && rs.getExpression() instanceof MethodInvocation mi) {
            return mi.arguments().size() == method.parameters().size();
        }
        if (stmt instanceof ExpressionStatement es && es.getExpression() instanceof MethodInvocation mi) {
            return mi.arguments().size() == method.parameters().size();
        }
        return false;
    }
}
