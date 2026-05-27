/*
 * SPDX-FileCopyrightText: Copyright (c) 2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "webrtcDataChannel.h"

WebrtcDataChannel* WebrtcDataChannel::m_instance = nullptr;

void WebrtcDataChannel::addChannelObserver(std::string clientId, rtc::scoped_refptr<webrtc::DataChannelInterface> channel)
{
	std::lock_guard<std::mutex> channelMap(m_channelMapMutex);
	auto itr = m_channelObserverMap.find(clientId);
	if (itr == m_channelObserverMap.end())
	{
		std::lock_guard<std::mutex> lock(m_listenerMutex);
		auto remoteChannel = std::make_shared<WebrtcDataChannelOberver>(channel, m_listeners);
		m_channelObserverMap.insert(make_pair(clientId, remoteChannel));
		LOG(info) << "created data channel with ID " << clientId << endl;
	}
	else
	{
		LOG(error) << "Data channel already present for ID " << clientId << endl;
	}
}
void WebrtcDataChannel::removeChannelObserver(std::string clientId)
{
	LOG(info) << __PRETTY_FUNCTION__ << endl;
	std::lock_guard<std::mutex> channelMap(m_channelMapMutex);
	auto itr = m_channelObserverMap.find(clientId);
	if (itr != m_channelObserverMap.end())
	{
		m_channelObserverMap.erase(clientId);
	}
	else
	{
		LOG(error) << "Data channel not present for ID " << clientId << endl;
	}
}

bool WebrtcDataChannel::sendMessageOnDataChannel(std::string clientId, std::string msg)
{
	std::lock_guard<std::mutex> channelMap(m_channelMapMutex);
	auto itr = m_channelObserverMap.find(clientId);
	bool isSendSuccess = false;
	if (itr != m_channelObserverMap.end())
	{
		isSendSuccess = itr->second->sendMessage(msg);
	}
	else
	{
		LOG(error) << "No data channel found with ID " << clientId << endl;
	}
	return isSendSuccess;
}

void WebrtcDataChannel::sendMessageOnAllDataChannels(std::string msg)
{
	std::lock_guard<std::mutex> channelMap(m_channelMapMutex);
	for (auto itr: m_channelObserverMap)
	{
		itr.second->sendMessage(msg);
	}
}

bool WebrtcDataChannel::isConnected(std::string clientId)
{
	std::lock_guard<std::mutex> channelMap(m_channelMapMutex);
	auto itr = m_channelObserverMap.find(clientId);
	if (itr != m_channelObserverMap.end())
	{
		return true;
	}
	return false;
}

// register the listener to peer connection observer
void WebrtcDataChannel::registerListener(IDataChannelListener *listener)
{
	{
		std::lock_guard<std::mutex> lock(m_listenerMutex);
		m_listeners.insert(listener);
	}
	{
		std::lock_guard<std::mutex> lock(m_channelMapMutex);
		for (auto itr: m_channelObserverMap)
		{
			auto observer = itr.second;
			observer->registerListener(listener);
		}
	}
}

// de-register the listener from peer connection observer
void WebrtcDataChannel::deRegisterListener(IDataChannelListener *listener)
{
	LOG(info) << __PRETTY_FUNCTION__ << endl;
	{
		std::lock_guard<std::mutex> lock(m_listenerMutex);
		m_listeners.erase(listener);
	}
	{
		std::lock_guard<std::mutex> lock(m_channelMapMutex);
		for (auto itr: m_channelObserverMap)
		{
			auto observer = itr.second;
			observer->deRegisterListener(listener);
		}
	}
}
