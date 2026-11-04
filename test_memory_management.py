from aurora.knowledge.fact import Fact
from aurora.memory.manager import MemoryManager


memory = MemoryManager()


# Create a test fact
fact = Fact(
    category="Preference",
    entity="User",
    attribute="Favourite Language",
    value="Python",
    priority=4,
)


# TEST 1: Remember
memory_id = memory.remember(fact)

print("Saved memory ID:", memory_id)


# TEST 2: Duplicate prevention
duplicate_id = memory.remember(fact)

print("Duplicate returned ID:", duplicate_id)

if memory_id == duplicate_id:
    print("PASS: Duplicate prevented.")
else:
    print("FAIL: Duplicate was created.")


# TEST 3: Search
print("\nSearch results for 'Python':")

results = memory.search("Python")

for item in results:
    print(
        f"[{item.id}] "
        f"{item.attribute} = {item.value}"
    )


# TEST 4: Edit
print("\nChanging Python to Java...")

success = memory.edit(
    memory_id,
    "Java"
)

print("Edit successful:", success)


# TEST 5: Verify edit
print("\nSearch results for 'Java':")

results = memory.search("Java")

for item in results:
    print(
        f"[{item.id}] "
        f"{item.attribute} = {item.value}"
    )


# TEST 6: Delete
print("\nDeleting test memory...")

success = memory.forget(memory_id)

print("Delete successful:", success)