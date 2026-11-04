# AURORA Memory System Design

Version: 1.0
Status: Draft

---

# Purpose

The memory system allows AURORA to retain meaningful information over time.

Its goal is NOT to save every conversation.

Its goal is to build an organized knowledge base that helps AURORA understand the user, their goals, and the world.

---

# Design Principles

1. Remember important information automatically.

2. Ignore temporary or irrelevant information.

3. Store structured facts instead of raw sentences.

4. Allow the user to edit, remove, or inspect memories.

5. Separate memory from reasoning.

6. Every module should have one responsibility.

---

# Memory Pipeline

User Input

↓

Fact Extractor

↓

Memory Evaluator

↓

Memory Manager

↓

Memory Store

↓

Future Database (SQLite)

---

# Memory Types

## Working Memory

Purpose:
Current conversation.

Lifetime:
Until AURORA closes.

Examples:

- Previous messages
- Current discussion
- Temporary context

---

## Semantic Memory

Purpose:
Stores facts.

Examples:

- User's name
- User preferences
- Programming languages
- Scientific facts

Lifetime:
Permanent until edited or removed.

---

## Episodic Memory

Purpose:
Stores experiences.

Examples:

- User created Project AURORA.
- User completed the Provider Layer.
- User started learning Python.

Lifetime:
Permanent until edited or removed.

---

# Fact Structure

Every memory is stored as a Fact.

Fields:

- id
- category
- entity
- attribute
- value
- priority
- confidence
- created
- last_used
- times_used

---

# Categories

Current categories:

Personal

Preferences

Projects

Goals

Skills

Research

Education

Relationships

Observations

Future categories can be added without changing existing code.

---

# Priority Levels

5 - Critical

Examples

- User name
- Long-term goals
- Important projects

---

4 - Important

Examples

- Favourite language
- Interests
- Career aspirations

---

3 - Useful

Examples

- Frequently used tools
- Preferred coding style

---

2 - Minor

Examples

- Favourite movie
- Favourite colour

---

1 - Temporary

Usually ignored.

Examples

- Weather
- Greetings
- Small talk

---

# User Control

The user can always:

View memories

Edit memories

Delete memories

Search memories

Nothing is permanently locked.

---

# Responsibilities

## Fact Extractor

Converts natural language into structured facts.

Does NOT save anything.

---

## Memory Evaluator

Determines whether a fact should be stored.

Assigns priority if necessary.

---

## Memory Manager

Stores, edits, deletes and retrieves memories.

Does NOT interpret natural language.

---

## Memory Store

Stores memory objects.

Current implementation:

Python list

Future:

SQLite

Later:

Vector Database

Knowledge Graph

---

# Future Goals

Semantic search

Automatic fact merging

Duplicate detection

Memory confidence updates

Knowledge graph

Relationship inference

Context summarisation

Long-term reasoning

---

# Philosophy

AURORA should not remember everything.

AURORA should remember what matters.

Memory exists to improve reasoning, not to archive conversations.