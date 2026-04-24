def heap_sort(in_list: list[int]) -> None:
    """
    Mutates elements in lst by using the heap data structure
    """

    def max_heapify(heap_size: int, index: int) -> None:
        left, right = 2 * index + 1, 2 * index + 2
        largest = index
        if left < heap_size and in_list[left] > in_list[largest]:
            largest = left
        if right < heap_size and in_list[right] > in_list[largest]:
            largest = right
        if largest != index:
            in_list[index], in_list[largest] = in_list[largest], in_list[index]
            max_heapify(heap_size, largest)

    # heapify original lst
    for i in range(len(in_list) // 2 - 1, -1, -1):
        max_heapify(len(in_list), i)

    # use heap to sort elements
    for i in range(len(in_list) - 1, 0, -1):
        # swap last element with first element
        in_list[i], in_list[0] = in_list[0], in_list[i]
        # note that we reduce the heap size by 1 every iteration
        max_heapify(i, 0)


if __name__ == "__main__":
    lst = [7, 3, 2, 5, 6, 10, 9, 8, 1]
    heap_sort(lst)
    assert lst == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
