# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import random

# implementation
import numpy as np
from shapely.geometry import Polygon


# Define individual in the population
class Individual:
    """
    Represents an individual solution in the genetic algorithm for pallet placement optimization.
    Each individual contains a genome representing pallet positions and methods to evaluate their fitness.

    :ivar list[tuple[float, float]] genome: List of (x,y) coordinates representing pallet positions.
    :ivar float pallet_width: Width of each pallet.
    :ivar float area_width: Width of the available area.
    :ivar float area_height: Height of the available area.
    :ivar list[Polygon] existing_pallets: List of Polygon objects representing existing pallets.
    :ivar int fitness: Fitness score calculated from pallet placement.
    :ivar list[int] bad_genome_indicators: Indicators for which genome positions have conflicts.
    """

    def __init__(self, genome: list[tuple[float, float]], pallet_width: float, area_width: float, area_height: float, existing_pallets: list[Polygon]) -> None:
        # genome = [(x1,y1),(x2,y2),...,(xn,yn)]
        # existing_pallets = [Polygon(),Polygon(),...,Polygon()]
        # bottom-left corner of area is (0,0)
        self.genome = genome
        self.existing_pallets = existing_pallets
        self.pallet_width = pallet_width
        self.area_width = area_width
        self.area_height = area_height
        self.fitness = self.calculate_fitness()

    def calculate_fitness(self) -> int:
        """
        Calculate the fitness of the individual based on pallet placement.
        Fitness is the negative number of crossings between pallets and against walls/existing pallets.
        Lower fitness (more negative) indicates worse placement.

        :return int: Fitness score (negative number of crossings).
        """
        bad_genome_indicators = []
        count = 0
        placed_pallets = [self.convert_pallet_bottom_left_loc_to_polygon(loc, self.pallet_width) for loc in self.genome]
        all_pallets = placed_pallets + self.existing_pallets
        for idx1 in range(len(self.genome)):
            poly1 = placed_pallets[idx1]

            bad_genome_indicator = 0

            for idx2 in range(len(all_pallets)):
                poly2 = all_pallets[idx2]
                if idx1 != idx2 and self.two_pallets_cross(poly1, poly2):
                    count += 1
                    bad_genome_indicator = 1
            bad_genome_indicators.append(bad_genome_indicator)
        self.bad_genome_indicators = bad_genome_indicators

        return -count

    def convert_pallet_bottom_left_loc_to_polygon(self, loc: tuple[float, float], pallet_width: float = 1) -> Polygon:
        """
        Convert a pallet's bottom-left location to a Polygon object.

        :param tuple loc: (x,y) coordinates of the bottom-left corner.
        :param float pallet_width: Width of the pallet (default: 1).
        :return Polygon: Polygon object representing the pallet.
        """
        x, y = loc
        x_min = x
        x_max = x + pallet_width
        y_min = y
        y_max = y + pallet_width

        polygon = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])

        return polygon

    def two_pallets_cross(self, poly1: Polygon, poly2: Polygon) -> int:
        """
        Check if two pallets (represented as polygons) intersect.

        :param Polygon poly1: First pallet polygon.
        :param Polygon poly2: Second pallet polygon.
        :return int: 1 if pallets intersect, 0 otherwise.
        """
        flag_cross = 0
        if poly1.intersects(poly2) and not poly1.touches(poly2):
            flag_cross = 1

        return flag_cross


def generate_random_coordinates(x_candidate_list: list[float], y_candidate_list: list[float]) -> tuple[float, float]:
    """
    Generate a random (x, y) coordinate from the given candidate lists.

    :param list x_candidate_list: List of possible x coordinates.
    :param list y_candidate_list: List of possible y coordinates.
    :return tuple[float, float]: Tuple representing the random (x, y) coordinate.

    Examples::
        >>> x_candidates = [0, 1, 2, 3]
        >>> y_candidates = [0, 1, 2, 3]
        >>> coord = generate_random_coordinates(x_candidates, y_candidates)
        >>> print(f"Random coordinate: {coord}")
    """
    x = random.sample(x_candidate_list, 1)[0]
    y = random.sample(y_candidate_list, 1)[0]
    return (x, y)


def generate_genome(pallet_number: int, x_candidate_list: list[float], y_candidate_list: list[float]) -> list[tuple[float, float]]:
    """
    Generate a random genome (list of coordinates) for pallet placement.

    :param int pallet_number: Number of pallets to place.
    :param list x_candidate_list: List of possible x coordinates.
    :param list y_candidate_list: List of possible y coordinates.
    :return list[tuple[float, float]]: List of (x,y) coordinates for pallet placement.

    Examples::
        >>> x_candidates = [0, 1, 2, 3]
        >>> y_candidates = [0, 1, 2, 3]
        >>> genome = generate_genome(3, x_candidates, y_candidates)
        >>> print(f"Generated genome: {genome}")
    """
    genome = []
    for _ in range(pallet_number):
        genome.append(generate_random_coordinates(x_candidate_list, y_candidate_list))

    return genome


def first_greater_than(lst: list[float], k: float) -> float | None:
    """
    Find the first element in a list that is greater than or equal to k.

    :param list lst: Input list of numbers.
    :param float k: Threshold value.
    :return float or None: First element >= k, or None if no such element exists.

    Examples::
        >>> numbers = [1, 3, 5, 7, 9]
        >>> result = first_greater_than(numbers, 4)  # Returns 5
    """
    for x in lst:
        if x >= k:
            return x
    return None  # Return None if no element is greater


def last_smaller_than(lst: list[float], k: float) -> float | None:
    """
    Find the last element in a list that is smaller than k.

    :param list lst: Input list of numbers.
    :param float k: Threshold value.
    :return float | None: Last element < k, or None if no such element exists.

    Examples::
        >>> numbers = [1, 3, 5, 7, 9]
        >>> result = last_smaller_than(numbers, 6)  # Returns 5
    """
    for idx in range(len(lst)):
        if lst[idx] > k:
            if idx > 0:
                return lst[idx - 1]
            else:
                return None
    return None  # Return None if no element is greater


def generate_genome_dense_packed_from_bottom_left(
    pallet_number: int, x_candidate_list: list[float], y_candidate_list: list[float], pallet_width: float, x_first: int = 1
) -> list[tuple[float, float]]:
    """
    Generate a genome with pallets densely packed from the bottom-left corner.
    Pallets are placed in a grid pattern, either prioritizing x or y direction first.

    :param int pallet_number: Number of pallets to place.
    :param list x_candidate_list: List of possible x coordinates.
    :param list y_candidate_list: List of possible y coordinates.
    :param float pallet_width: Width of each pallet.
    :param int x_first: If 1, fill x direction first; if 0, fill y direction first.
    :return list[tuple[float, float]]: List of (x,y) coordinates for pallet placement.

    Examples::
        >>> x_candidates = [0, 1, 2, 3]
        >>> y_candidates = [0, 1, 2, 3]
        >>> genome = generate_genome_dense_packed_from_bottom_left(4, x_candidates, y_candidates, 1.0)
        >>> print(f"Dense packed genome: {genome}")
    """
    genome = []
    x = x_candidate_list[0]
    y = y_candidate_list[0]
    genome.append((x, y))

    while len(genome) < pallet_number:
        if x_first:
            x_new = first_greater_than(x_candidate_list, x + pallet_width)
            if x_new is not None:
                x = x_new
                genome.append((x, y))
            else:
                x = x_candidate_list[0]
                y_new = first_greater_than(y_candidate_list, y + pallet_width)
                if y_new is not None:
                    y = y_new
                    genome.append((x, y))
        else:
            y_new = first_greater_than(y_candidate_list, y + pallet_width)
            if y_new is not None:
                y = y_new
                genome.append((x, y))
            else:
                y = y_candidate_list[0]
                x_new = first_greater_than(x_candidate_list, x + pallet_width)
                if x_new is not None:
                    x = x_new
                    genome.append((x, y))
    return genome


def generate_genome_dense_packed_from_top_left(
    pallet_number: int, x_candidate_list: list[float], y_candidate_list: list[float], pallet_width: float, x_first: int = 1
) -> list[tuple[float, float]]:
    """
    Generate a genome with pallets densely packed from the top-left corner.
    Pallets are placed in a grid pattern, either prioritizing x or y direction first.
    Starts from the top-left corner and moves right/down.

    :param int pallet_number: Number of pallets to place.
    :param list x_candidate_list: List of possible x coordinates.
    :param list y_candidate_list: List of possible y coordinates.
    :param float pallet_width: Width of each pallet.
    :param int x_first: If 1, fill x direction first; if 0, fill y direction first.
    :return list[tuple[float, float]]: List of (x,y) coordinates for pallet placement.

    Examples::
        >>> x_candidates = [0, 1, 2, 3]
        >>> y_candidates = [0, 1, 2, 3]
        >>> genome = generate_genome_dense_packed_from_top_left(4, x_candidates, y_candidates, 1.0)
        >>> print(f"Dense packed genome from top-left: {genome}")
    """
    genome = []
    x = x_candidate_list[0]
    y = y_candidate_list[-1]
    genome.append((x, y))

    while len(genome) < pallet_number:
        if x_first:
            x_new = first_greater_than(x_candidate_list, x + pallet_width)
            if x_new is not None:
                x = x_new
                genome.append((x, y))
            else:
                x = x_candidate_list[0]
                y_new = last_smaller_than(y_candidate_list, y - pallet_width)
                if y_new is not None:
                    y = y_new
                    genome.append((x, y))
        else:
            y_new = last_smaller_than(y_candidate_list, y - pallet_width)
            if y_new is not None:
                y = y_new
                genome.append((x, y))
            else:
                y = y_candidate_list[-1]
                x_new = first_greater_than(x_candidate_list, x + pallet_width)
                if x_new is not None:
                    x = x_new
                    genome.append((x, y))
    return genome


def generate_genome_dense_packed_from_top_right(
    pallet_number: int, x_candidate_list: list[float], y_candidate_list: list[float], pallet_width: float, x_first: int = 1
) -> list[tuple[float, float]]:
    """
    Generate a genome with pallets densely packed from the top-right corner.
    Pallets are placed in a grid pattern, either prioritizing x or y direction first.
    Starts from the top-right corner and moves left/down.

    :param int pallet_number: Number of pallets to place.
    :param list x_candidate_list: List of possible x coordinates.
    :param list y_candidate_list: List of possible y coordinates.
    :param float pallet_width: Width of each pallet.
    :param int x_first: If 1, fill x direction first; if 0, fill y direction first.
    :return list[tuple[float, float]]: List of (x,y) coordinates for pallet placement.

    Examples::
        >>> x_candidates = [0, 1, 2, 3]
        >>> y_candidates = [0, 1, 2, 3]
        >>> genome = generate_genome_dense_packed_from_top_right(4, x_candidates, y_candidates, 1.0)
        >>> print(f"Dense packed genome from top-right: {genome}")
    """
    genome = []
    x = x_candidate_list[-1]
    y = y_candidate_list[-1]
    genome.append((x, y))

    while len(genome) < pallet_number:
        if x_first:
            x_new = last_smaller_than(x_candidate_list, x - pallet_width)
            if x_new is not None:
                x = x_new
                genome.append((x, y))
            else:
                x = x_candidate_list[-1]
                y_new = last_smaller_than(y_candidate_list, y - pallet_width)
                if y_new is not None:
                    y = y_new
                    genome.append((x, y))
        else:
            y_new = last_smaller_than(y_candidate_list, y - pallet_width)
            if y_new is not None:
                y = y_new
                genome.append((x, y))
            else:
                y = y_candidate_list[-1]
                x_new = last_smaller_than(x_candidate_list, x - pallet_width)
                if x_new is not None:
                    x = x_new
                    genome.append((x, y))
    return genome


def generate_genome_dense_packed_from_bottom_right(
    pallet_number: int, x_candidate_list: list[float], y_candidate_list: list[float], pallet_width: float, x_first: int = 1
) -> list[tuple[float, float]]:
    """
    Generate a genome with pallets densely packed from the bottom-right corner.
    Pallets are placed in a grid pattern, either prioritizing x or y direction first.
    Starts from the bottom-right corner and moves left/up.

    :param int pallet_number: Number of pallets to place.
    :param list x_candidate_list: List of possible x coordinates.
    :param list y_candidate_list: List of possible y coordinates.
    :param float pallet_width: Width of each pallet.
    :param int x_first: If 1, fill x direction first; if 0, fill y direction first.
    :return list[tuple[float, float]]: List of (x,y) coordinates for pallet placement.

    Examples::
        >>> x_candidates = [0, 1, 2, 3]
        >>> y_candidates = [0, 1, 2, 3]
        >>> genome = generate_genome_dense_packed_from_bottom_right(4, x_candidates, y_candidates, 1.0)
        >>> print(f"Dense packed genome from bottom-right: {genome}")
    """
    genome = []
    x = x_candidate_list[-1]
    y = y_candidate_list[0]
    genome.append((x, y))

    while len(genome) < pallet_number:
        if x_first:
            x_new = last_smaller_than(x_candidate_list, x - pallet_width)
            if x_new is not None:
                x = x_new
                genome.append((x, y))
            else:
                x = x_candidate_list[-1]
                y_new = first_greater_than(y_candidate_list, y + pallet_width)
                if y_new is not None:
                    y = y_new
                    genome.append((x, y))
        else:
            y_new = first_greater_than(y_candidate_list, y + pallet_width)
            if y_new is not None:
                y = y_new
                genome.append((x, y))
            else:
                y = y_candidate_list[0]
                x_new = last_smaller_than(x_candidate_list, x - pallet_width)
                if x_new is not None:
                    x = x_new
                    genome.append((x, y))
    return genome


def crossover(parent1: Individual, parent2: Individual) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """
    Perform crossover between two parent genomes to create two child genomes.
    Uses a single random cut point to combine parent genomes.

    :param Individual parent1: First parent individual.
    :param Individual parent2: Second parent individual.
    :return tuple[list[tuple[float, float]], list[tuple[float, float]]]: Tuple of two child genomes.

    Examples::
        >>> child1, child2 = crossover(parent1, parent2)
        >>> print(f"Child 1 genome: {child1}")
        >>> print(f"Child 2 genome: {child2}")
    """
    child1_genome = parent1.genome
    child2_genome = parent2.genome
    if len(parent1.genome) > 1:
        cut = random.randint(1, len(parent1.genome) - 1)
        child1_genome = parent1.genome[:cut] + parent2.genome[cut:]
        child2_genome = parent2.genome[:cut] + parent1.genome[cut:]
    return child1_genome, child2_genome


def mutate(
    genome: list[tuple[float, float]], mutation_rate: float, x_candidate_list: list[float], y_candidate_list: list[float], bad_genome_indicators: list[int] = [], bad_genome_mutate_multiplier: float = 5
) -> list[tuple[float, float]]:
    """
    Mutate a genome by randomly changing some pallet positions.
    Bad genome indicators can increase mutation probability for problematic positions.

    :param list genome: List of (x,y) coordinates to mutate.
    :param float mutation_rate: Base probability of mutation for each position.
    :param list x_candidate_list: List of possible x coordinates.
    :param list y_candidate_list: List of possible y coordinates.
    :param list bad_genome_indicators: List indicating problematic positions (1 for bad, 0 for good).
    :param float bad_genome_mutate_multiplier: Multiplier for mutation rate of bad positions.
    :return list[tuple[float, float]]: Mutated genome.

    Examples::
        >>> genome = [(0,0), (1,1), (2,2)]
        >>> mutated = mutate(genome, 0.1, x_candidates, y_candidates)
        >>> print(f"Mutated genome: {mutated}")
    """
    new_genome = genome[:]
    for i in range(len(genome)):
        if bad_genome_indicators:
            bad_genome_indicator = bad_genome_indicators[i]
            if bad_genome_indicator:
                mutation_rate *= bad_genome_mutate_multiplier
        if random.random() < mutation_rate:
            new_genome[i] = generate_random_coordinates(x_candidate_list, y_candidate_list)

    return new_genome


# def mutate(genome, mutation_rate, x_candidate_list, y_candidate_list, \
#            bad_genome_indicators = [], bad_genome_mutate_multiplier = 5):

#     new_genome = genome[:]
#     for i in range(len(genome)):
#         if bad_genome_indicators:
#             bad_genome_indicator = bad_genome_indicators[i]
#             if bad_genome_indicator:
#                 mutation_rate = 1
#             else:
#                 mutation_rate = 0.1
#         if random.random() < mutation_rate:
#             new_genome[i] = generate_random_coordinates(x_candidate_list, y_candidate_list)

#     return new_genome


def mutate_individual(individual: Individual, mutation_rate: float, x_candidate_list: list[float], y_candidate_list: list[float], bad_genome_mutate_multiplier: float = 5) -> Individual:
    """
    Mutate an individual's genome and create a new individual with the mutated genome.

    :param Individual individual: Individual to mutate.
    :param float mutation_rate: Base probability of mutation for each position.
    :param list x_candidate_list: List of possible x coordinates.
    :param list y_candidate_list: List of possible y coordinates.
    :param float bad_genome_mutate_multiplier: Multiplier for mutation rate of bad positions.
    :return Individual: New individual with mutated genome.

    Examples::
        >>> mutated = mutate_individual(individual, 0.1, x_candidates, y_candidates)
        >>> print(f"Mutated individual fitness: {mutated.fitness}")
    """
    genome = individual.genome
    bad_genome_indicators = individual.bad_genome_indicators

    new_genome = mutate(
        genome, mutation_rate, x_candidate_list, y_candidate_list, bad_genome_indicators, bad_genome_mutate_multiplier
    )

    individual_new = Individual(
        new_genome, individual.pallet_width, individual.area_width, individual.area_height, individual.existing_pallets
    )

    return individual_new


def genetic_algorithm(
    area_width: float,
    area_height: float,
    pallet_width: float,
    pallet_number: int,
    existing_pallets: list[Polygon],
    grid_size: float,
    population_size: int = 50,
    generations: int = 100,
    mutation_rate: float = 0.1,
    use_good_init: int = 1,
    strategy: int = 1,
) -> Individual:
    """
    Run a genetic algorithm to find optimal pallet placement.

    :param float area_width: Width of the available area.
    :param float area_height: Height of the available area.
    :param float pallet_width: Width of each pallet.
    :param int pallet_number: Number of pallets to place.
    :param list existing_pallets: List of Polygon objects representing existing pallets.
    :param float grid_size: Size of the grid for discretizing the space.
    :param int population_size: Size of the population (default: 50).
    :param int generations: Number of generations to run (default: 100).
    :param float mutation_rate: Probability of mutation (default: 0.1).
    :param int use_good_init: Whether to use good initial solutions (1 for yes, 0 for no).
    :param int strategy: Strategy for evolution (default: 1).
    :return Individual: Best individual found.

    Examples::
        >>> solution = genetic_algorithm(
        ...     area_width=10.0,
        ...     area_height=10.0,
        ...     pallet_width=1.0,
        ...     pallet_number=5,
        ...     existing_pallets=[],
        ...     grid_size=0.1
        ... )
        >>> print(f"Best solution fitness: {solution.fitness}")
    """
    num_grid_per_pallet = int(pallet_width / grid_size)
    num_grid_width = int(area_width / grid_size)
    num_grid_height = int(area_height / grid_size)
    x_candidate_list = np.linspace(0, area_width - pallet_width, num_grid_width - num_grid_per_pallet + 1)
    y_candidate_list = np.linspace(0, area_height - pallet_width, num_grid_height - num_grid_per_pallet + 1)
    x_candidate_list = [round(v, 1) for v in x_candidate_list]
    y_candidate_list = [round(v, 1) for v in y_candidate_list]

    population = [
        Individual(
            generate_genome(pallet_number, x_candidate_list, y_candidate_list),
            pallet_width,
            area_width,
            area_height,
            existing_pallets,
        )
        for _ in range(population_size)
    ]

    if use_good_init:

        good_init1 = Individual(
            generate_genome_dense_packed_from_bottom_left(
                pallet_number, x_candidate_list, y_candidate_list, pallet_width, x_first=1
            ),
            pallet_width,
            area_width,
            area_height,
            existing_pallets,
        )
        good_init2 = Individual(
            generate_genome_dense_packed_from_bottom_left(
                pallet_number, x_candidate_list, y_candidate_list, pallet_width, x_first=0
            ),
            pallet_width,
            area_width,
            area_height,
            existing_pallets,
        )
        good_init3 = Individual(
            generate_genome_dense_packed_from_top_left(
                pallet_number, x_candidate_list, y_candidate_list, pallet_width, x_first=1
            ),
            pallet_width,
            area_width,
            area_height,
            existing_pallets,
        )
        good_init4 = Individual(
            generate_genome_dense_packed_from_top_left(
                pallet_number, x_candidate_list, y_candidate_list, pallet_width, x_first=0
            ),
            pallet_width,
            area_width,
            area_height,
            existing_pallets,
        )
        good_init5 = Individual(
            generate_genome_dense_packed_from_bottom_right(
                pallet_number, x_candidate_list, y_candidate_list, pallet_width, x_first=1
            ),
            pallet_width,
            area_width,
            area_height,
            existing_pallets,
        )
        good_init6 = Individual(
            generate_genome_dense_packed_from_bottom_right(
                pallet_number, x_candidate_list, y_candidate_list, pallet_width, x_first=0
            ),
            pallet_width,
            area_width,
            area_height,
            existing_pallets,
        )
        good_init7 = Individual(
            generate_genome_dense_packed_from_top_right(
                pallet_number, x_candidate_list, y_candidate_list, pallet_width, x_first=1
            ),
            pallet_width,
            area_width,
            area_height,
            existing_pallets,
        )
        good_init8 = Individual(
            generate_genome_dense_packed_from_top_right(
                pallet_number, x_candidate_list, y_candidate_list, pallet_width, x_first=0
            ),
            pallet_width,
            area_width,
            area_height,
            existing_pallets,
        )

        population.append(good_init1)
        population.append(good_init2)
        population.append(good_init3)
        population.append(good_init4)
        population.append(good_init5)
        population.append(good_init6)
        population.append(good_init7)
        population.append(good_init8)

    for _ in range(generations):
        population.sort(key=lambda ind: ind.fitness, reverse=True)

        if population[0].fitness == 0:  # Ideal case: 0 crosses against other pallets
            break

        next_generation = population[:10]  # Elitism

        while len(next_generation) < population_size:

            if strategy == 1:
                # strategy 1: crossover + regular mutation
                parent1, parent2 = random.sample(population[: int(population_size / 2)], 2)
                child1_genome, child2_genome = crossover(parent1, parent2)
                child1_genome = mutate(child1_genome, mutation_rate, x_candidate_list, y_candidate_list)
                child2_genome = mutate(child2_genome, mutation_rate, x_candidate_list, y_candidate_list)
                next_generation.extend(
                    [
                        Individual(child1_genome, pallet_width, area_width, area_height, existing_pallets),
                        Individual(child2_genome, pallet_width, area_width, area_height, existing_pallets),
                    ]
                )
            if strategy == 2:
                # strategy 2: crossover + bad genome mutation
                parent1, parent2 = random.sample(population[: int(population_size / 2)], 2)
                child1_genome, child2_genome = crossover(parent1, parent2)
                child1 = Individual(child1_genome, pallet_width, area_width, area_height, existing_pallets)
                child2 = Individual(child2_genome, pallet_width, area_width, area_height, existing_pallets)
                next_generation.extend(
                    [
                        mutate_individual(child1, mutation_rate, x_candidate_list, y_candidate_list),
                        mutate_individual(child2, mutation_rate, x_candidate_list, y_candidate_list),
                    ]
                )
            if strategy == 3:
                # strategy 3: clone + bad genome mutation
                child = random.sample(population[: int(population_size / 2)], 1)[0]
                next_generation.extend([mutate_individual(child, mutation_rate, x_candidate_list, y_candidate_list)])

        population = next_generation

    solution = population[0]

    return solution
