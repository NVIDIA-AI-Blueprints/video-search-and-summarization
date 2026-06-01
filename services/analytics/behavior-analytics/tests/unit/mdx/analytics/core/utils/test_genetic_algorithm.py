# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import pytest
from shapely.geometry import Polygon

from mdx.analytics.core.utils.genetic_algorithm import (
    Individual,
    generate_random_coordinates,
    generate_genome,
    first_greater_than,
    last_smaller_than,
    generate_genome_dense_packed_from_bottom_left,
    generate_genome_dense_packed_from_top_left,
    generate_genome_dense_packed_from_top_right,
    generate_genome_dense_packed_from_bottom_right,
    crossover,
    mutate,
    mutate_individual,
    genetic_algorithm,
)


class TestIndividual:
    """Tests for Individual class."""

    @pytest.fixture
    def simple_individual(self):
        """Create a simple individual for testing."""
        genome = [(0, 0), (2, 0), (0, 2)]
        return Individual(
            genome=genome,
            pallet_width=1.0,
            area_width=5.0,
            area_height=5.0,
            existing_pallets=[]
        )

    def test_individual_creation(self, simple_individual):
        """Test that individual is created with correct attributes."""
        assert len(simple_individual.genome) == 3
        assert simple_individual.pallet_width == 1.0
        assert simple_individual.area_width == 5.0
        assert simple_individual.area_height == 5.0

    def test_fitness_no_overlaps(self, simple_individual):
        """Test fitness when no pallets overlap."""
        # With genome [(0,0), (2,0), (0,2)] and width 1, pallets don't overlap
        assert simple_individual.fitness == 0

    def test_fitness_with_overlaps(self):
        """Test fitness when pallets overlap."""
        # Overlapping positions
        genome = [(0, 0), (0.5, 0.5)]  # These will overlap
        individual = Individual(
            genome=genome,
            pallet_width=1.0,
            area_width=5.0,
            area_height=5.0,
            existing_pallets=[]
        )
        # Fitness should be negative due to overlaps
        assert individual.fitness < 0

    def test_convert_pallet_to_polygon(self, simple_individual):
        """Test converting pallet location to polygon."""
        polygon = simple_individual.convert_pallet_bottom_left_loc_to_polygon(
            (0, 0), pallet_width=1.0
        )
        assert isinstance(polygon, Polygon)
        bounds = polygon.bounds
        assert bounds == (0, 0, 1, 1)

    def test_two_pallets_cross(self, simple_individual):
        """Test two_pallets_cross method."""
        poly1 = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
        poly2 = Polygon([(1, 1), (3, 1), (3, 3), (1, 3)])
        result = simple_individual.two_pallets_cross(poly1, poly2)
        assert result == 1

    def test_bad_genome_indicators(self, simple_individual):
        """Test that bad_genome_indicators is populated."""
        # For non-overlapping pallets, all indicators should be 0
        assert hasattr(simple_individual, 'bad_genome_indicators')
        assert all(indicator == 0 for indicator in simple_individual.bad_genome_indicators)


class TestGenerateRandomCoordinates:
    """Tests for generate_random_coordinates function."""

    def test_returns_tuple(self):
        """Test that function returns a tuple."""
        x_list = [0, 1, 2, 3]
        y_list = [0, 1, 2, 3]
        result = generate_random_coordinates(x_list, y_list)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_coordinates_from_lists(self):
        """Test that coordinates come from provided lists."""
        x_list = [0, 1, 2]
        y_list = [10, 20, 30]
        for _ in range(20):
            x, y = generate_random_coordinates(x_list, y_list)
            assert x in x_list
            assert y in y_list


class TestGenerateGenome:
    """Tests for generate_genome function."""

    def test_genome_length(self):
        """Test that genome has correct number of coordinates."""
        pallet_number = 5
        x_list = [0, 1, 2, 3, 4]
        y_list = [0, 1, 2, 3, 4]
        result = generate_genome(pallet_number, x_list, y_list)
        assert len(result) == pallet_number

    def test_genome_contains_valid_coords(self):
        """Test that genome contains valid coordinates."""
        pallet_number = 3
        x_list = [0, 1, 2]
        y_list = [10, 20]
        result = generate_genome(pallet_number, x_list, y_list)
        for x, y in result:
            assert x in x_list
            assert y in y_list


class TestFirstGreaterThan:
    """Tests for first_greater_than function."""

    def test_finds_first_greater(self):
        """Test finding first element >= k."""
        lst = [1, 3, 5, 7, 9]
        assert first_greater_than(lst, 4) == 5
        assert first_greater_than(lst, 5) == 5

    def test_returns_none_if_none_found(self):
        """Test returns None when no element >= k."""
        lst = [1, 2, 3]
        assert first_greater_than(lst, 10) is None

    def test_first_element(self):
        """Test when first element is >= k."""
        lst = [5, 6, 7]
        assert first_greater_than(lst, 4) == 5


class TestLastSmallerThan:
    """Tests for last_smaller_than function."""

    def test_finds_last_smaller(self):
        """Test finding last element < k."""
        lst = [1, 3, 5, 7, 9]
        assert last_smaller_than(lst, 6) == 5

    def test_returns_none_if_none_found(self):
        """Test returns None when first element is already > k."""
        lst = [5, 6, 7]
        # First element 5 > 4, and idx=0, so returns None
        assert last_smaller_than(lst, 4) is None


class TestDensePackedGenomes:
    """Tests for dense packed genome generation functions."""

    @pytest.fixture
    def coordinate_lists(self):
        """Create coordinate lists for testing."""
        return {
            'x': [0, 1, 2, 3, 4],
            'y': [0, 1, 2, 3, 4]
        }

    def test_dense_packed_bottom_left(self, coordinate_lists):
        """Test dense packing from bottom-left."""
        result = generate_genome_dense_packed_from_bottom_left(
            pallet_number=4,
            x_candidate_list=coordinate_lists['x'],
            y_candidate_list=coordinate_lists['y'],
            pallet_width=1.0
        )
        assert len(result) == 4
        # First pallet should be at bottom-left
        assert result[0] == (0, 0)

    def test_dense_packed_top_left(self, coordinate_lists):
        """Test dense packing from top-left."""
        result = generate_genome_dense_packed_from_top_left(
            pallet_number=4,
            x_candidate_list=coordinate_lists['x'],
            y_candidate_list=coordinate_lists['y'],
            pallet_width=1.0
        )
        assert len(result) == 4
        # First pallet should be at top-left
        assert result[0] == (0, 4)

    def test_dense_packed_top_right(self, coordinate_lists):
        """Test dense packing from top-right."""
        result = generate_genome_dense_packed_from_top_right(
            pallet_number=4,
            x_candidate_list=coordinate_lists['x'],
            y_candidate_list=coordinate_lists['y'],
            pallet_width=1.0
        )
        assert len(result) == 4
        # First pallet should be at top-right
        assert result[0] == (4, 4)

    def test_dense_packed_bottom_right(self, coordinate_lists):
        """Test dense packing from bottom-right."""
        result = generate_genome_dense_packed_from_bottom_right(
            pallet_number=4,
            x_candidate_list=coordinate_lists['x'],
            y_candidate_list=coordinate_lists['y'],
            pallet_width=1.0
        )
        assert len(result) == 4
        # First pallet should be at bottom-right
        assert result[0] == (4, 0)


class TestCrossover:
    """Tests for crossover function."""

    def test_crossover_produces_two_children(self):
        """Test that crossover produces two child genomes."""
        parent1 = Individual(
            genome=[(0, 0), (1, 0), (2, 0)],
            pallet_width=1.0,
            area_width=5.0,
            area_height=5.0,
            existing_pallets=[]
        )
        parent2 = Individual(
            genome=[(0, 1), (1, 1), (2, 1)],
            pallet_width=1.0,
            area_width=5.0,
            area_height=5.0,
            existing_pallets=[]
        )
        child1, child2 = crossover(parent1, parent2)
        assert len(child1) == 3
        assert len(child2) == 3

    def test_crossover_single_gene(self):
        """Test crossover with single gene genome."""
        parent1 = Individual(
            genome=[(0, 0)],
            pallet_width=1.0,
            area_width=5.0,
            area_height=5.0,
            existing_pallets=[]
        )
        parent2 = Individual(
            genome=[(1, 1)],
            pallet_width=1.0,
            area_width=5.0,
            area_height=5.0,
            existing_pallets=[]
        )
        child1, child2 = crossover(parent1, parent2)
        # With single gene, children should be copies of parents
        assert child1 == [(0, 0)]
        assert child2 == [(1, 1)]


class TestMutate:
    """Tests for mutate function."""

    def test_mutation_preserves_length(self):
        """Test that mutation preserves genome length."""
        genome = [(0, 0), (1, 0), (2, 0)]
        x_list = [0, 1, 2, 3, 4]
        y_list = [0, 1, 2, 3, 4]
        result = mutate(genome, 0.5, x_list, y_list)
        assert len(result) == len(genome)

    def test_mutation_with_zero_rate(self):
        """Test that zero mutation rate doesn't change genome."""
        genome = [(0, 0), (1, 0), (2, 0)]
        x_list = [0, 1, 2, 3, 4]
        y_list = [0, 1, 2, 3, 4]
        result = mutate(genome, 0.0, x_list, y_list)
        assert result == genome


class TestMutateIndividual:
    """Tests for mutate_individual function."""

    def test_returns_new_individual(self):
        """Test that mutation returns a new Individual."""
        individual = Individual(
            genome=[(0, 0), (1, 0), (2, 0)],
            pallet_width=1.0,
            area_width=5.0,
            area_height=5.0,
            existing_pallets=[]
        )
        x_list = [0, 1, 2, 3, 4]
        y_list = [0, 1, 2, 3, 4]
        result = mutate_individual(individual, 0.5, x_list, y_list)
        assert isinstance(result, Individual)
        assert result is not individual


class TestGeneticAlgorithm:
    """Tests for genetic_algorithm function."""

    def test_returns_individual(self):
        """Test that genetic algorithm returns an Individual."""
        result = genetic_algorithm(
            area_width=5.0,
            area_height=5.0,
            pallet_width=1.0,
            pallet_number=3,
            existing_pallets=[],
            grid_size=1.0,
            population_size=10,
            generations=5
        )
        assert isinstance(result, Individual)

    def test_with_existing_pallets(self):
        """Test genetic algorithm with existing pallets."""
        existing = [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])]
        result = genetic_algorithm(
            area_width=5.0,
            area_height=5.0,
            pallet_width=1.0,
            pallet_number=2,
            existing_pallets=existing,
            grid_size=1.0,
            population_size=10,
            generations=5
        )
        assert isinstance(result, Individual)

    def test_with_different_strategies(self):
        """Test genetic algorithm with different strategies."""
        for strategy in [1, 2, 3]:
            result = genetic_algorithm(
                area_width=5.0,
                area_height=5.0,
                pallet_width=1.0,
                pallet_number=2,
                existing_pallets=[],
                grid_size=1.0,
                population_size=10,
                generations=5,
                strategy=strategy
            )
            assert isinstance(result, Individual)

    def test_with_good_init(self):
        """Test genetic algorithm with good initialization."""
        result = genetic_algorithm(
            area_width=5.0,
            area_height=5.0,
            pallet_width=1.0,
            pallet_number=2,
            existing_pallets=[],
            grid_size=1.0,
            population_size=10,
            generations=5,
            use_good_init=1
        )
        assert isinstance(result, Individual)

