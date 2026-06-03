// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <memory>

#include "opentelemetry/sdk/metrics/aggregation/aggregation.h"
#include "opentelemetry/sdk/metrics/aggregation/drop_aggregation.h"
#include "opentelemetry/sdk/metrics/aggregation/histogram_aggregation.h"
#include "opentelemetry/sdk/metrics/aggregation/lastvalue_aggregation.h"
#include "opentelemetry/sdk/metrics/aggregation/sum_aggregation.h"
#include "opentelemetry/sdk/metrics/data/point_data.h"
#include "opentelemetry/sdk/metrics/instruments.h"
#include "opentelemetry/version.h"

OPENTELEMETRY_BEGIN_NAMESPACE
namespace sdk
{
namespace metrics
{
class AggregationConfig;

class DefaultAggregation
{
public:
  static std::unique_ptr<Aggregation> CreateAggregation(
      const opentelemetry::sdk::metrics::InstrumentDescriptor &instrument_descriptor,
      const AggregationConfig *aggregation_config)
  {
    switch (instrument_descriptor.type_)
    {
      case InstrumentType::kCounter:
      case InstrumentType::kObservableCounter:
        return (instrument_descriptor.value_type_ == InstrumentValueType::kLong)
                   ? std::move(std::unique_ptr<Aggregation>(new LongSumAggregation(true)))
                   : std::move(std::unique_ptr<Aggregation>(new DoubleSumAggregation(true)));
      case InstrumentType::kUpDownCounter:
      case InstrumentType::kObservableUpDownCounter:
        return (instrument_descriptor.value_type_ == InstrumentValueType::kLong)
                   ? std::move(std::unique_ptr<Aggregation>(new LongSumAggregation(false)))
                   : std::move(std::unique_ptr<Aggregation>(new DoubleSumAggregation(false)));
        break;
      case InstrumentType::kHistogram: {
        if (instrument_descriptor.value_type_ == InstrumentValueType::kLong)
        {
          return (std::unique_ptr<Aggregation>(new LongHistogramAggregation(aggregation_config)));
        }
        else
        {
          return (std::unique_ptr<Aggregation>(new DoubleHistogramAggregation(aggregation_config)));
        }

        break;
      }
      case InstrumentType::kObservableGauge:
        return (instrument_descriptor.value_type_ == InstrumentValueType::kLong)
                   ? std::move(std::unique_ptr<Aggregation>(new LongLastValueAggregation()))
                   : std::move(std::unique_ptr<Aggregation>(new DoubleLastValueAggregation()));
        break;
      default:
        return std::unique_ptr<Aggregation>(new DropAggregation());
    };
  }

  static std::unique_ptr<Aggregation> CreateAggregation(
      AggregationType aggregation_type,
      InstrumentDescriptor instrument_descriptor,
      const AggregationConfig *aggregation_config = nullptr)
  {
    switch (aggregation_type)
    {
      case AggregationType::kDrop:
        return std::unique_ptr<Aggregation>(new DropAggregation());
        break;
      case AggregationType::kHistogram:
        if (instrument_descriptor.value_type_ == InstrumentValueType::kLong)
        {
          return std::unique_ptr<Aggregation>(new LongHistogramAggregation(aggregation_config));
        }
        else
        {
          return std::unique_ptr<Aggregation>(new DoubleHistogramAggregation(aggregation_config));
        }
        break;
      case AggregationType::kLastValue:
        if (instrument_descriptor.value_type_ == InstrumentValueType::kLong)
        {
          return std::unique_ptr<Aggregation>(new LongLastValueAggregation());
        }
        else
        {
          return std::unique_ptr<Aggregation>(new DoubleLastValueAggregation());
        }
        break;
      case AggregationType::kSum: {
        bool is_monotonic = true;
        if (instrument_descriptor.type_ == InstrumentType::kUpDownCounter ||
            instrument_descriptor.type_ == InstrumentType::kObservableUpDownCounter ||
            instrument_descriptor.type_ == InstrumentType::kHistogram)
        {
          is_monotonic = false;
        }
        if (instrument_descriptor.value_type_ == InstrumentValueType::kLong)
        {
          return std::unique_ptr<Aggregation>(new LongSumAggregation(is_monotonic));
        }
        else
        {
          return std::unique_ptr<Aggregation>(new DoubleSumAggregation(is_monotonic));
        }
        break;
      }
      default:
        return DefaultAggregation::CreateAggregation(instrument_descriptor, aggregation_config);
    }
  }

  static std::unique_ptr<Aggregation> CloneAggregation(AggregationType aggregation_type,
                                                       InstrumentDescriptor instrument_descriptor,
                                                       const Aggregation &to_copy)
  {
    const PointType point_data = to_copy.ToPoint();
    switch (aggregation_type)
    {
      case AggregationType::kDrop:
        return std::unique_ptr<Aggregation>(new DropAggregation());
      case AggregationType::kHistogram:
        if (instrument_descriptor.value_type_ == InstrumentValueType::kLong)
        {
          return std::unique_ptr<Aggregation>(
              new LongHistogramAggregation(nostd::get<HistogramPointData>(point_data)));
        }
        else
        {
          return std::unique_ptr<Aggregation>(
              new DoubleHistogramAggregation(nostd::get<HistogramPointData>(point_data)));
        }
      case AggregationType::kLastValue:
        if (instrument_descriptor.value_type_ == InstrumentValueType::kLong)
        {
          return std::unique_ptr<Aggregation>(
              new LongLastValueAggregation(nostd::get<LastValuePointData>(point_data)));
        }
        else
        {
          return std::unique_ptr<Aggregation>(
              new DoubleLastValueAggregation(nostd::get<LastValuePointData>(point_data)));
        }
      case AggregationType::kSum:
        if (instrument_descriptor.value_type_ == InstrumentValueType::kLong)
        {
          return std::unique_ptr<Aggregation>(
              new LongSumAggregation(nostd::get<SumPointData>(point_data)));
        }
        else
        {
          return std::unique_ptr<Aggregation>(
              new DoubleSumAggregation(nostd::get<SumPointData>(point_data)));
        }
      default:
        return DefaultAggregation::CreateAggregation(instrument_descriptor, nullptr);
    }
  }
};

}  // namespace metrics
}  // namespace sdk
OPENTELEMETRY_END_NAMESPACE
